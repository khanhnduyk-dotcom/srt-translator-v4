import asyncio
import time
import json
import re
import random
import httpx
import sys
import os
import threading
import queue as thread_queue
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import MODEL_NAME, ACCOUNTS, RATE_LIMITS, TOTAL_WORKERS

# ── Global registry for health dashboard ──
# Maps file_id → KeyManager; server.py reads this for /api/worker-health
_active_key_managers: dict = {}

# Ánh xạ mã ngôn ngữ ngắn → tên đầy đủ bằng tiếng Anh
LANG_MAP = {
    'vi': 'Vietnamese',
    'en': 'English',
    'ja': 'Japanese',
    'ko': 'Korean',
    'zh': 'Chinese',
    'fr': 'French',
    'de': 'German',
    'es': 'Spanish',
    'th': 'Thai',
    'id': 'Indonesian',
    'pt': 'Portuguese',
    'ru': 'Russian',
    'ar': 'Arabic',
    'hi': 'Hindi',
    'it': 'Italian',
}

# Detect CJK (Chinese/Japanese/Korean) characters remaining in translated text
def has_cjk(text):
    """Returns True if text contains CJK characters (Chinese/Japanese/Korean ideographs)."""
    # CJK Unified Ideographs + Extension A + CJK Compatibility Ideographs
    return bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]', text))

def has_source_chars(text, target_lang):
    """Check if translated text still has source language chars based on target."""
    if target_lang in ('Vietnamese', 'English', 'French', 'German', 'Spanish', 'Italian', 'Portuguese', 'Indonesian'):
        return has_cjk(text)
    return False

def validate_length_ratio(source_text: str, trans_text: str) -> bool:
    """
    Kiểm tra tỷ lệ độ dài translation so với source có hợp lý không.
    Tránh trường hợp AI swap text giữa các dòng (dòng 2 char → dịch ra 20 char).
    Returns True nếu tỷ lệ ổn, False nếu nghi ngờ bị swap.
    """
    s_len = len(source_text.strip())
    t_len = len(trans_text.strip())
    if s_len == 0 or t_len == 0:
        return True  # Empty = fine, don't block
    
    # Very short sources (≤5 chars) shouldn't produce very long translations (>5x)
    if s_len <= 5 and t_len > s_len * 5 and t_len > 20:
        return False
    # Very long sources (>20 chars) shouldn't produce empty-like translations (<3 chars)
    if s_len > 20 and t_len < 3:
        return False
    return True

def parse_translated_response(translated_str: str, subs_ref: list) -> list:
    """Parse AI response thành danh sách bản dịch, xử lý:
    - Multi-line subtitle: [N] line1\\nline2 → gom vào 1 entry
    - Duplicate [N]: first-write-wins, skip duplicate
    - AI notes/explanations: lọc bỏ dòng Note:, *, (, ---
    - KHÔNG fallback positional để tránh lệch dòng
    - Per-line length ratio check để phát hiện AI swap số
    
    Returns: list[str] có len == len(subs_ref)
    """
    num_expected = len(subs_ref)
    
    # Phase 1: Parse format [N] text, hỗ trợ multi-line
    trans_map = {}  # {num: text}
    lines = translated_str.split('\n')
    current_num = None
    current_text_parts = []
    
    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue
        
        # Check if line starts with [N]
        match = re.match(r'\[(\d+)\]\s*(.*)', line_stripped)
        if match:
            # Save previous entry if any
            if current_num is not None and current_num not in trans_map:
                if 1 <= current_num <= num_expected:
                    text_result = '\n'.join(current_text_parts).strip()
                    src_text = subs_ref[current_num - 1]['text']
                    # Length ratio validation — detect AI swap
                    if text_result and validate_length_ratio(src_text, text_result):
                        trans_map[current_num] = text_result
                    elif text_result:
                        print(f"[AccuracyGuard] Line [{current_num}] length suspicious: src={len(src_text)}ch → trans={len(text_result)}ch — kept anyway but flagged")
                        trans_map[current_num] = text_result  # Keep but log
            
            current_num = int(match.group(1))
            current_text_parts = [match.group(2).strip()] if match.group(2).strip() else []
        elif current_num is not None:
            # Multi-line subtitle continuation
            if _is_ai_note(line_stripped):
                continue
            current_text_parts.append(line_stripped)
    
    # Save last entry
    if current_num is not None and current_num not in trans_map:
        if 1 <= current_num <= num_expected:
            text_result = '\n'.join(current_text_parts).strip()
            if text_result:
                trans_map[current_num] = text_result
    
    # Phase 2: Build result array
    match_count = len(trans_map)
    trans_texts = []
    
    if match_count >= num_expected * 0.5:
        # Enough [N] matches — use trans_map, return source for missing lines (will trigger retry)
        for idx in range(1, num_expected + 1):
            if idx in trans_map and trans_map[idx]:
                trans_texts.append(trans_map[idx])
            else:
                # Return source text for missing lines — cleanup phase detects CJK and retries
                trans_texts.append(subs_ref[idx - 1]['text'])
        return trans_texts
    
    # Phase 3: AI didn't use [N] format at all (<50% match)
    # CRITICAL: DO NOT do positional fallback — it causes line-shift bugs!
    # Instead: return source text for ALL lines → retry mechanism will handle it
    print(f"[AccuracyGuard] Batch returned <50% [N] format ({match_count}/{num_expected}). Skipping positional fallback — returning source for retry.")
    return [sub['text'] for sub in subs_ref]


def _is_ai_note(text: str) -> bool:
    """Detect AI-generated notes/explanations that are NOT translations."""
    t = text.strip()
    if not t:
        return True
    # Common AI note patterns
    note_prefixes = ('note:', 'note :', '注:', '注意:', '(note', '(注',
                     '---', '***', '===', 'translation:', 'translated:')
    if t.lower().startswith(note_prefixes):
        return True
    # Lines that are entirely in parentheses (explanations)
    if (t.startswith('(') and t.endswith(')') and len(t) > 40 and
        not any(c in t for c in ['→', '-->'])):
        return True
    if t.startswith('*') and t.endswith('*') and len(t) > 10:
        return True
    return False


# ============================================================
# EXCLAMATION MAP — Thán từ Trung → Việt (pre-process trước khi dịch)
# ============================================================
EXCLAMATION_MAP = {
    "啊": "À", "啊啊": "À à", "啊啊啊": "À à à", "啊啊哈": "Á á ha ha",
    "嗯": "Ừm", "嗯嗯": "Ừm ừm", "嗯嗯嗯": "Ừm ừm ừm",
    "哼": "Hừ", "哈": "Ha", "哈哈": "Ha ha", "哈哈哈": "Ha ha ha", "哈哈哈哈": "Ha ha ha ha",
    "嘶": "Xì", "喂": "Này", "哇": "Ồ", "哦": "Ồ", "呀": "Á",
    "唉": "Ai", "嘿": "Này", "噢": "Ồ", "喔": "Ồ", "哎": "Ái",
    "呵": "Hơ", "呵呵": "Hơ hơ", "嗷": "Ao", "咦": "Ơ", "呸": "Phì",
    "嘘": "Suỵt", "咳": "Khụ", "呜": "Hu", "呜呜": "Hu hu", "呜呜呜": "Hu hu hu",
    "嗨": "Này", "嚯": "Hố", "唔": "Ừm", "噗": "Phụt", "呃": "Ơ",
    "嘿嘿": "Hê hê", "嘿嘿嘿": "Hê hê hê", "嘻嘻": "Hi hi", "嘻嘻嘻": "Hi hi hi",
    "哎呀": "Ái chà", "哎哟": "Ối giời", "天哪": "Trời ơi",
    "我的天": "Trời ơi", "天啊": "Trời ơi", "妈呀": "Trời ơi",
}

# Sắp xếp key dài trước để match greedy
_SORTED_EXCL_KEYS = sorted(EXCLAMATION_MAP.keys(), key=len, reverse=True)

def replace_exclamation(text: str) -> str:
    """Thay thế thán từ Trung ở toàn bộ block hoặc đầu câu."""
    norm = re.sub(r'\s+', '', text.strip())
    if norm in EXCLAMATION_MAP:
        return EXCLAMATION_MAP[norm]
    stripped = text.strip()
    for zh_key in _SORTED_EXCL_KEYS:
        if stripped.startswith(zh_key):
            rest = stripped[len(zh_key):].lstrip()
            if rest:
                return f"{EXCLAMATION_MAP[zh_key]} {rest}"
            break
    return text


def get_endpoint(model_name: str) -> str:
    """Xác định API endpoint dựa vào tên model."""
    name = model_name.lower()
    if name.startswith('gemini'):
        return "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    elif name.startswith('gpt-') or name.startswith('o3-'):
        return "https://api.openai.com/v1/chat/completions"
    elif name.startswith('deepseek'):
        return "https://api.deepseek.com/v1/chat/completions"
    # Mặc định dùng Groq (llama, mixtral, gemma v.v.)
    return "https://api.groq.com/openai/v1/chat/completions"


def get_rate_limits_for_model(model_name: str) -> dict:
    """Auto-detect rate limits dựa vào tên model.
    Gemini Paid Tier 1:
      - Flash models: 2000 RPM, 4,000,000 TPM
      - Pro models: 360 RPM, 2,000,000 TPM
    Groq Free: 30 RPM, 6000 TPM
    OpenAI: 500 RPM, 200000 TPM
    DeepSeek: 60 RPM, 100000 TPM
    """
    name = model_name.lower()
    if name.startswith('gemini'):
        if 'pro' in name:
            return {"rpm": 360, "tpm": 2000000}
        else:  # flash, flash-lite, etc.
            return {"rpm": 2000, "tpm": 4000000}
    elif name.startswith('gpt-') or name.startswith('o3-'):
        return {"rpm": 500, "tpm": 200000}
    elif name.startswith('deepseek'):
        return {"rpm": 60, "tpm": 100000}
    # Mặc định Groq
    return {"rpm": 30, "tpm": 6000}


def detect_key_provider(key: str) -> str:
    """Auto-detect provider dựa vào prefix của API key."""
    if key.startswith('gsk_'):
        return 'groq'
    elif key.startswith('AIza'):
        return 'gemini'
    elif key.startswith('sk-'):
        return 'openai'
    return 'unknown'


def parse_tagged_keys(keys_list: list, default_model: str) -> list:
    """Parse danh sách keys có thể có tag provider.
    
    Formats hỗ trợ:
      - 'groq:gsk_abc123'          → Groq key, dùng default groq model
      - 'gemini:AIzaSy...'         → Gemini key, dùng default gemini model  
      - 'groq:llama-3.1-8b:gsk_x'  → Groq key với model cụ thể
      - 'gsk_abc123'               → Auto-detect = Groq
      - 'AIzaSy...'                → Auto-detect = Gemini
      - 'sk-abc...'                → Auto-detect = OpenAI
      - 'plain_key'                → Dùng default_model
    
    Returns: list of {key, model, endpoint, provider, rpm, tpm}
    """
    DEFAULT_MODELS = {
        'groq': 'llama-3.3-70b-versatile',
        'gemini': 'gemini-2.5-flash',
        'openai': 'gpt-4o-mini',
        'deepseek': 'deepseek-chat',
    }
    
    parsed = []
    for raw in keys_list:
        raw = raw.strip()
        if not raw:
            continue
        
        provider = None
        model = None
        key = raw
        
        # Check for tag format: "provider:key" or "provider:model:key"
        if ':' in raw:
            parts = raw.split(':', maxsplit=2)
            tag = parts[0].lower()
            if tag in ('groq', 'gemini', 'openai', 'deepseek'):
                provider = tag
                if len(parts) == 3:
                    # provider:model:key
                    model = parts[1]
                    key = parts[2]
                else:
                    # provider:key
                    key = parts[1]
        
        # Auto-detect provider from key prefix if not tagged
        if not provider:
            provider = detect_key_provider(key)
        
        # If still unknown, detect from default_model
        if provider == 'unknown':
            provider_from_model = default_model.lower()
            if provider_from_model.startswith('gemini'):
                provider = 'gemini'
            elif provider_from_model.startswith('gpt') or provider_from_model.startswith('o3'):
                provider = 'openai'
            elif provider_from_model.startswith('deepseek'):
                provider = 'deepseek'
            else:
                provider = 'groq'
        
        # Set model
        if not model:
            model = DEFAULT_MODELS.get(provider, default_model)
        
        # Get endpoint and rate limits
        endpoint = get_endpoint(model)
        limits = get_rate_limits_for_model(model)
        
        parsed.append({
            'key': key,
            'model': model,
            'endpoint': endpoint,
            'provider': provider,
            'rpm': limits['rpm'],
            'tpm': limits['tpm'],
        })
    
    # Summary
    providers = {}
    for p in parsed:
        prov = p['provider']
        if prov not in providers:
            providers[prov] = {'count': 0, 'rpm': 0, 'model': p['model']}
        providers[prov]['count'] += 1
        providers[prov]['rpm'] += p['rpm']
    
    for prov, info in providers.items():
        print(f"  → {prov.upper()}: {info['count']} keys × {info['rpm']//info['count']} RPM = {info['rpm']} RPM total (model: {info['model']})")
    
    return parsed


async def analyze_characters(subtitles, key_manager, model_name, target_lang):
    """Bước 0: Phân tích nhân vật từ 80 dòng đầu, tạo quy tắc xưng hô."""
    # Lấy tối đa 80 dòng text đầu tiên
    sample_lines = [sub['text'] for sub in subtitles[:80]]
    sample_text = "\n".join(sample_lines)
    
    analysis_prompt = f"""You are a professional subtitle analyst. Analyze the following subtitle text (in its original language) and extract:

1. **All character names** appearing in the dialogue. For each name, provide:
   - Original name
   - Phonetic transliteration in {target_lang}
   - Brief description (gender, role: protagonist/antagonist/supporting/system)

2. **Addressing rules** (đại từ xưng hô) for each pair of characters who interact. Specify:
   - How Character A addresses themselves (xưng) and calls Character B (gọi)
   - How Character B addresses themselves and calls Character A
   - Use appropriate {target_lang} pronouns based on their relationship

3. **Common terms/units** that appear and their correct {target_lang} translations.

Output format (follow EXACTLY):
[CHARACTERS]
- OriginalName → TransliteratedName (description)

[ADDRESSING]
N. CharA ↔ CharB
   - CharA xưng: "...", gọi CharB: "..."
   - CharB xưng: "...", gọi CharA: "..."

[TERMS]
- OriginalTerm → Translation

Subtitle text:
{sample_text}"""
    
    try:
        acc_name, api_key, _, _ = await key_manager.get_available_key(len(sample_text) // 4 + 200)
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are an expert subtitle analyst. Always respond with structured analysis."},
                    {"role": "user", "content": analysis_prompt}
                ],
                "stream": False,
                "temperature": 0.2
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            endpoint = get_endpoint(model_name)
            
            resp = await client.post(endpoint, json=payload, headers=headers)
            if resp.status_code == 200:
                analysis = resp.json()['choices'][0]['message']['content'].strip()
                print(f"\n--- Phan tich nhan vat hoan tat ({len(analysis)} chars):")
                print(analysis[:500] + ("..." if len(analysis) > 500 else ""))
                return analysis
            else:
                print(f"!! Phan tich nhan vat that bai (HTTP {resp.status_code}). Bo qua buoc nay.")
                return ""
    except Exception as e:
        print(f"!! Loi phan tich nhan vat: {e}. Bo qua buoc nay.")
        return ""

class SubtitleChunker:
    @staticmethod
    def parse_srt(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
        
        # Normalize line endings
        content = content.replace('\r\n', '\n').replace('\r', '\n')
        
        blocks = content.split('\n\n')
        subtitles = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            lines = block.split('\n')
            if len(lines) >= 3:
                try:
                    sub_id = int(lines[0].strip())
                except ValueError:
                    continue
                times = lines[1].strip()
                idx_arrow = times.find('-->')
                if idx_arrow != -1:
                    start = times[:idx_arrow].strip()
                    end = times[idx_arrow + 3:].strip()
                else:
                    start, end = "", ""
                text = '\n'.join(l.strip() for l in lines[2:])
                subtitles.append({
                    'id': sub_id,
                    'start': start,
                    'end': end,
                    'text': text
                })
        return subtitles

    @staticmethod
    def chunk_subtitles(file_id, subtitles, max_lines=30):
        jobs = []
        chunk_idx = 0
        
        for i in range(0, len(subtitles), max_lines):
            chunk_subs = subtitles[i:i + max_lines]
            start_time = chunk_subs[0]['start']
            end_time = chunk_subs[-1]['end']
            
            # Sử dụng định dạng đánh số [1] [2] [3]... để AI giữ đúng số dòng
            numbered_lines = []
            for idx, sub in enumerate(chunk_subs, 1):
                numbered_lines.append(f"[{idx}] {sub['text']}")
            original_text = "\n".join(numbered_lines)
            
            job = {
                'file_id': file_id,
                'chunk_index': chunk_idx,
                'start_time': start_time,
                'end_time': end_time,
                'original_text': original_text,
                'num_lines': len(chunk_subs),
                'subs_ref': chunk_subs # Lưu lại reference để reconstruct SRT sau
            }
            jobs.append(job)
            chunk_idx += 1
            
        return jobs


class ProviderExhaustedError(Exception):
    """Raised when all API keys are exhausted (quota exceeded, not just rate-limited)."""
    pass


class JobQueue:
    """Thread-safe job queue that works across multiple event loops.
    
    Uses queue.Queue (thread-safe) internally, with async wrapper for
    coroutine-based workers. This is CRITICAL for ThreadedWorkerPool
    where each thread has its own asyncio event loop.
    """
    def __init__(self):
        self.queue = thread_queue.Queue()

    async def put(self, job):
        self.queue.put(job)

    async def get(self):
        # Non-blocking async wrapper around thread-safe queue
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.queue.get)

    def task_done(self):
        self.queue.task_done()
        
    def empty(self):
        return self.queue.empty()
    
    def qsize(self):
        return self.queue.qsize()


class AccountRateLimiter:
    def __init__(self, rpm, tpm):
        self.rpm = rpm
        self.tpm = tpm
        self.requests = []  # Danh sách timestamp của các request
        self.tokens = []    # Danh sách tuple (timestamp, token_count)
        self.lock = threading.Lock()  # Thread-safe lock

    async def try_consume_capacity(self, estimated_tokens: int):
        with self.lock:
            now = time.time()
            self.requests = [t for t in self.requests if now - t < 60]
            self.tokens = [(t, count) for t, count in self.tokens if now - t < 60]
            
            current_rpm = len(self.requests)
            current_tpm = sum(count for _, count in self.tokens)
            
            if current_rpm < self.rpm and current_tpm + estimated_tokens <= self.tpm:
                self.requests.append(now)
                self.tokens.append((now, estimated_tokens))
                return True
            return False

class APIKeyManager:
    def __init__(self, flat_keys, limiters):
        self.keys = []
        for info in flat_keys:
            self.keys.append({
                "account_name": info["account_name"],
                "key": info["key"],
                "model": info.get("model", ""),
                "endpoint": info.get("endpoint", ""),
                "limiter": limiters[info["account_name"]],
                "sleep_until": 0,
                # Usage tracking
                "requests_ok": 0,
                "requests_fail": 0,
                "requests_429": 0,
                "tokens_used": 0,
            })
        self.lock = threading.Lock()  # Thread-safe lock
        self.idx = 0
        self.start_time = time.time()
        self.limiters = limiters  # Store for ThreadedWorkerPool access
        self.exhausted = False  # True when all keys are dead
        self.pause_event = None  # Set externally for pause/resume
        self.resume_event = None  # Set externally for pause/resume

    async def get_available_key(self, estimated_tokens: int, timeout: float = 60.0):
        """Returns (account_name, api_key, model, endpoint).
        Raises ProviderExhaustedError if no key available within timeout."""
        start_wait = time.time()
        while True:
            now = time.time()
            
            # Check timeout — all keys might be permanently dead
            if now - start_wait > timeout:
                self.exhausted = True
                raise ProviderExhaustedError(
                    f"All API keys exhausted after {timeout}s wait. "
                    f"Keys: {len(self.keys)}, all sleeping or rate-limited."
                )
            
            with self.lock:
                for _ in range(len(self.keys)):
                    i = self.idx
                    self.idx = (self.idx + 1) % len(self.keys)
                    k = self.keys[i]
                    
                    if k["sleep_until"] <= now:
                        has_cap = await k["limiter"].try_consume_capacity(estimated_tokens)
                        if has_cap:
                            return k["account_name"], k["key"], k["model"], k["endpoint"]
                            
            await asyncio.sleep(0.5)

    async def add_keys(self, new_flat_keys: list, new_limiters: dict):
        """Hot-inject new keys (e.g., when user switches model after exhaustion)."""
        with self.lock:
            for info in new_flat_keys:
                # Add limiter if not exists
                if info['account_name'] not in new_limiters:
                    continue
                self.keys.append({
                    "account_name": info["account_name"],
                    "key": info["key"],
                    "model": info.get("model", ""),
                    "endpoint": info.get("endpoint", ""),
                    "limiter": new_limiters[info["account_name"]],
                    "sleep_until": 0,
                    "requests_ok": 0,
                    "requests_fail": 0,
                    "requests_429": 0,
                    "tokens_used": 0,
                })
            self.exhausted = False
            print(f"[KeyManager] Hot-injected {len(new_flat_keys)} new keys. Total: {len(self.keys)}")

    async def mark_sleep(self, api_key, wait_time):
        with self.lock:
            for k in self.keys:
                if k["key"] == api_key:
                    k["sleep_until"] = time.time() + wait_time
                    break

    async def record_usage(self, api_key, tokens: int, success: bool, is_429: bool = False):
        """Ghi nhan usage cho key sau moi request."""
        with self.lock:
            for k in self.keys:
                if k["key"] == api_key:
                    if success:
                        k["requests_ok"] += 1
                        k["tokens_used"] += tokens
                    elif is_429:
                        k["requests_429"] += 1
                    else:
                        k["requests_fail"] += 1
                    break

    def get_health_report(self):
        """Tra ve bao cao suc khoe cua tat ca keys."""
        elapsed = time.time() - self.start_time
        elapsed_min = max(elapsed / 60.0, 0.01)
        
        # Group by provider
        providers = {}
        key_details = []
        
        for k in self.keys:
            acc = k["account_name"]
            provider = acc.split("_")[0]  # Groq_1 -> Groq
            rpm_limit = k["limiter"].rpm
            
            if provider not in providers:
                providers[provider] = {"ok": 0, "fail": 0, "r429": 0, "tokens": 0, "keys": 0, "rpm_limit": rpm_limit}
            providers[provider]["ok"] += k["requests_ok"]
            providers[provider]["fail"] += k["requests_fail"]
            providers[provider]["r429"] += k["requests_429"]
            providers[provider]["tokens"] += k["tokens_used"]
            providers[provider]["keys"] += 1
            
            # Key masked
            masked = k["key"][:8] + "..." + k["key"][-4:] if len(k["key"]) > 12 else k["key"][:4] + "..."
            status = "active"
            if k["sleep_until"] > time.time():
                status = f"sleeping {int(k['sleep_until'] - time.time())}s"
            elif k["requests_429"] > 3:
                status = "rate-limited"
            
            key_details.append({
                "account": acc,
                "key_masked": masked,
                "model": k["model"],
                "ok": k["requests_ok"],
                "fail": k["requests_fail"],
                "r429": k["requests_429"],
                "tokens": k["tokens_used"],
                "status": status,
            })
        
        summary = []
        total_ok = 0
        total_tokens = 0
        for prov, info in providers.items():
            actual_rpm = round(info["ok"] / elapsed_min, 1)
            summary.append({
                "provider": prov,
                "keys": info["keys"],
                "requests_ok": info["ok"],
                "requests_fail": info["fail"],
                "requests_429": info["r429"],
                "tokens_used": info["tokens"],
                "actual_rpm": actual_rpm,
                "rpm_limit_per_key": info["rpm_limit"],
            })
            total_ok += info["ok"]
            total_tokens += info["tokens"]
        
        return {
            "elapsed_sec": round(elapsed, 1),
            "total_requests": total_ok,
            "total_tokens": total_tokens,
            "providers": summary,
            "keys": key_details,
        }


class AIWorker:
    def __init__(self, worker_id, key_manager: APIKeyManager, target_lang, default_model=None, character_context="", stream_queue: asyncio.Queue = None, shared_client: httpx.AsyncClient = None):
        self.worker_id = worker_id
        self.key_manager = key_manager
        self.target_lang = target_lang
        self.default_model = default_model or MODEL_NAME
        self.character_context = character_context
        self.client = shared_client or httpx.AsyncClient(timeout=120.0)
        self.owns_client = shared_client is None
        self.stream_queue = stream_queue
        self.consecutive_errors = 0  # Circuit breaker counter
        
    async def process_jobs(self, job_queue: JobQueue, results_dict: dict):
        while True:
            job = await job_queue.get()
            if job is None:  # Sentinel: no more jobs
                job_queue.task_done()
                break
                
            chunk_index = job['chunk_index']
            original_text = job['original_text']
            
            # Ước lượng số lượng token
            estimated_tokens = len(original_text) // 4 + 100
            
            num_lines = job.get('num_lines', 0)
            
            # Build character context block if available
            char_block = ""
            if self.character_context:
                char_block = f"""\n\n[CHARACTER REFERENCE - Use this for accurate names and addressing]
{self.character_context}

[END CHARACTER REFERENCE]\n"""
            
            sys_prompt = f"""You are an expert subtitle translator. Translate subtitles to {self.target_lang}.{char_block}

ABSOLUTE RULES (VIOLATION = FAILURE):
1. Input: numbered lines [1], [2], [3]... Output: EXACT same numbered lines [1], [2], [3]...
2. EVERY character in the output MUST be in {self.target_lang}. ABSOLUTELY ZERO Chinese/Japanese/Korean characters allowed in output.
3. Sound effects (嗯 嘶 哼 喂 啊 嗨 喔 呵 哈 嘿) → natural {self.target_lang} equivalents (Ùm, À, Hừ, A, ...).
4. Character names: use the transliterations from [CHARACTER REFERENCE] above. If not listed, transliterate phonetically.
5. ALL Chinese units/words must be translated: 两/两=lượng, 银子=bạc, 金子=vàng, 呢=nha/nhỉ, 呀=à/a, 万物=vạn vật, 万万=Vạn Vạn.
6. Output ONLY the [N] lines. No notes, explanations, or extra text.
7. If unsure about a character/word, STILL translate it to your best guess in {self.target_lang}. NEVER leave original characters.
8. NEVER swap the content of different line numbers. [1] must translate EXACTLY [1]'s source. [2] translates EXACTLY [2]'s source. No mixing allowed.

Example input:
[1] 你好
[2] 只是常田的她主来明还没有出息
[3] 社

WRONG output (swapping — FORBIDDEN):
[1] Xin chào
[2] ดู
[3] องหมัง? อืม...

CORRECT output:
[1] Xin chào
[2] Nhưng cô ấy vẫn chưa có thành tích gì
[3] Xã"""
            
            max_retries = 3
            
            # Tách error_retries (lỗi thật) khỏi 429 (rate limit tạm)
            error_retries = job.get('error_retries', 0)
            if error_retries > max_retries:
                print(f"[Worker {self.worker_id}] That bai sau {max_retries} lan thu o chunk {chunk_index}.")
                if job['file_id'] not in results_dict:
                    results_dict[job['file_id']] = {}
                results_dict[job['file_id']][chunk_index] = {
                    "job": job,
                    "translated_texts": original_text 
                }
                job_queue.task_done()
                continue
            
            # Circuit breaker: nếu 5 lỗi liên tiếp, sleep tất cả keys 30s
            if self.consecutive_errors >= 5:
                print(f"[Worker {self.worker_id}] Circuit breaker: 5 lỗi liên tiếp, dừng 30s...")
                await asyncio.sleep(30)
                self.consecutive_errors = 0
                
            # 1. Liên tục lấy Key khỏe nhất từ Manager (trả về cả model/endpoint riêng của key)
            try:
                account_name, api_key, key_model, key_endpoint = await self.key_manager.get_available_key(estimated_tokens)
            except ProviderExhaustedError as ex:
                print(f"[Worker {self.worker_id}] Provider exhausted: {ex}")
                # Emit exhaustion event and push job back
                if self.stream_queue:
                    await self.stream_queue.put({
                        "type": "provider_exhausted",
                        "message": str(ex),
                        "remaining_jobs": 1  # signal that work remains
                    })
                # Wait for resume signal (if pause/resume is set up)
                if self.key_manager.resume_event:
                    print(f"[Worker {self.worker_id}] Waiting for resume...")
                    await self.key_manager.resume_event.wait()
                    self.key_manager.resume_event.clear()
                    self.key_manager.exhausted = False
                    # Retry getting key after resume
                    try:
                        account_name, api_key, key_model, key_endpoint = await self.key_manager.get_available_key(estimated_tokens)
                    except ProviderExhaustedError:
                        # Still exhausted after resume — mark job failed
                        await job_queue.put(job)
                        job_queue.task_done()
                        continue
                else:
                    # No resume mechanism — push back and continue
                    await job_queue.put(job)
                    job_queue.task_done()
                    continue
            
            # Sử dụng model/endpoint của key nếu có, fallback về default
            use_model = key_model or self.default_model
            use_endpoint = key_endpoint or get_endpoint(self.default_model)
            
            print(f"[Worker {self.worker_id} | {account_name}] Chunk {chunk_index} via {use_model}...")

            # 2. Build payload SAU KHI đã có use_model
            payload = {
                "model": use_model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": original_text}
                ],
                "stream": False,
                "temperature": 0.3
            }
                
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            # ── Cookie mode: route through local /cookie-translate ──
            if api_key == 'cookie':
                _port = os.environ.get('PORT', '8000')
                use_endpoint = f"http://localhost:{_port}/cookie-translate"
                headers = {"Content-Type": "application/json"}  # No auth header needed
            
            try:
                # Jitter: random delay 0-300ms to avoid synchronized burst from multiple workers
                await asyncio.sleep(random.uniform(0, 0.3))
                resp = await self.client.post(use_endpoint, json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    translated_content = data['choices'][0]['message']['content']
                    
                    # Auto-validate: check output lines match input
                    expected_lines = num_lines or original_text.count('\n') + 1
                    output_lines = len([l for l in translated_content.strip().split('\n') if l.strip().startswith('[')])
                    if output_lines < expected_lines * 0.8 and error_retries < max_retries:
                        # Too few lines — auto-retry
                        print(f"[Worker {self.worker_id}] Chunk {chunk_index}: {output_lines}/{expected_lines} lines — auto-retry")
                        job['error_retries'] = error_retries + 1
                        await self.key_manager.record_usage(api_key, 0, success=True)
                        await job_queue.put(job)
                        job_queue.task_done()
                        continue
                    
                    # ── Per-line individual retry ──
                    # Check each translated line for remaining CJK (= untranslated / swap victim)
                    subs_ref_chunk = job.get('subs_ref', [])
                    if subs_ref_chunk and 'per_line_retried' not in job:
                        parsed_now = parse_translated_response(translated_content, subs_ref_chunk)
                        bad_indices = []  # 1-based indices of lines needing retry
                        for li, (src, trs) in enumerate(zip(subs_ref_chunk, parsed_now), 1):
                            if has_cjk(trs) and not has_cjk(src.get('text', '')):
                                # CJK in translation but NOT in source = bad line
                                bad_indices.append(li)
                            elif (len(src.get('text','').strip()) > 15 and len(trs.strip()) < 3):
                                # Long source → very short translation (swap victim)
                                bad_indices.append(li)

                        if bad_indices and len(bad_indices) <= 10:
                            # Build mini-batch with only bad lines
                            mini_lines = []
                            mini_subs = []
                            for bi in bad_indices:
                                s = subs_ref_chunk[bi - 1]
                                mini_lines.append(f"[{bi}] {s['text']}")
                                mini_subs.append(s)
                            mini_text = "\n".join(mini_lines)
                            print(f"[Worker {self.worker_id}] Per-line retry: {len(bad_indices)} bad lines {bad_indices} in chunk {chunk_index}")
                            
                            try:
                                mini_payload = {
                                    "model": use_model,
                                    "messages": [
                                        {"role": "system", "content": sys_prompt},
                                        {"role": "user", "content": mini_text}
                                    ],
                                    "stream": False, "temperature": 0.1
                                }
                                await asyncio.sleep(random.uniform(0.1, 0.4))
                                mini_resp = await self.client.post(use_endpoint, json=mini_payload, headers=headers)
                                if mini_resp.status_code == 200:
                                    mini_content = mini_resp.json()['choices'][0]['message']['content']
                                    mini_parsed = parse_translated_response(mini_content, mini_subs)
                                    # Merge: patch parsed_now with mini results
                                    for i, bi in enumerate(bad_indices):
                                        if i < len(mini_parsed) and mini_parsed[i] and not has_cjk(mini_parsed[i]):
                                            parsed_now[bi - 1] = mini_parsed[i]
                                    # Rebuild translated_content from patched parsed_now
                                    translated_content = "\n".join(
                                        f"[{i+1}] {t}" for i, t in enumerate(parsed_now)
                                    )
                                    print(f"[Worker {self.worker_id}] Per-line retry DONE: {len(bad_indices)} lines fixed")
                            except Exception as plex:
                                print(f"[Worker {self.worker_id}] Per-line retry error: {plex}")
                        
                        job['per_line_retried'] = True  # prevent infinite per-line retry loop
                    
                    # Ghi nhan usage
                    usage_tokens = data.get('usage', {}).get('total_tokens', estimated_tokens)
                    await self.key_manager.record_usage(api_key, usage_tokens, success=True)
                    self.consecutive_errors = 0
                    
                    # Lưu kết quả
                    res_obj = {
                        "job": job,
                        "translated_texts": translated_content
                    }
                    if job['file_id'] not in results_dict:
                        results_dict[job['file_id']] = {}
                    results_dict[job['file_id']][chunk_index] = res_obj
                    
                    if self.stream_queue:
                        await self.stream_queue.put({"type": "chunk", "data": res_obj})
                        
                    print(f"[Worker {self.worker_id} | {account_name}] Chunk {chunk_index} done ({usage_tokens} tok, {output_lines}/{expected_lines} lines)")
                    job_queue.task_done()
                    
                elif resp.status_code == 429: 
                    # 429 = rate limit → exponential backoff + jitter
                    retry_count = job.get('rate_limit_retries', 0) + 1
                    job['rate_limit_retries'] = retry_count
                    max_429_retries = 5
                    
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        base_wait = int(retry_after)
                    else:
                        # Exponential: 8s → 16s → 32s → 60s cap + ±20% jitter
                        base_wait = min(8 * (2 ** (retry_count - 1)), 60)
                    wait_time = base_wait * random.uniform(0.8, 1.2)
                    
                    if retry_count > max_429_retries:
                        # Skip job after 5 rate-limit hits
                        print(f"[Worker {self.worker_id}] Chunk {chunk_index} skip sau {max_429_retries} lan 429.")
                        if job['file_id'] not in results_dict:
                            results_dict[job['file_id']] = {}
                        results_dict[job['file_id']][chunk_index] = {"job": job, "translated_texts": original_text}
                        job_queue.task_done()
                    else:
                        print(f"[Worker {self.worker_id} | {account_name}] 429 #{retry_count}. Exp backoff {wait_time:.1f}s...")
                        await self.key_manager.mark_sleep(api_key, wait_time)
                        await self.key_manager.record_usage(api_key, 0, success=False, is_429=True)
                        await job_queue.put(job)
                        job_queue.task_done()

                else:
                    # Lỗi thật (500, 401, etc.) → tăng error_retries
                    print(f"[Worker {self.worker_id} | {account_name}] Loi API {resp.status_code} o chunk {chunk_index}: {resp.text[:100]}")
                    await self.key_manager.record_usage(api_key, 0, success=False)
                    await self.key_manager.mark_sleep(api_key, 5)
                    job['error_retries'] = error_retries + 1
                    self.consecutive_errors += 1
                    await job_queue.put(job)
                    job_queue.task_done()
                    
            except Exception as e:
                print(f"[Worker {self.worker_id} | Acc {account_name}] Loi Request o chunk {chunk_index}: {e}")
                await self.key_manager.mark_sleep(api_key, 5)
                job['error_retries'] = error_retries + 1
                self.consecutive_errors += 1
                await job_queue.put(job)
                job_queue.task_done()
                
        if self.owns_client:
            await self.client.aclose()



# ============================================================
# THREADED WORKER POOL — Đa luồng, mỗi luồng nhiều workers async
# ============================================================
class ThreadedWorkerPool:
    """Multi-threaded worker pool: N threads × M workers/thread.
    Mỗi thread có event loop riêng + httpx client riêng.
    Workers trong cùng thread share event loop (async concurrency).
    Threads chạy parallel (true parallelism cho I/O).
    """
    
    def __init__(self, flat_keys, key_manager, job_queue, results_dict,
                 target_lang, default_model, character_context,
                 stream_queue, total_jobs, max_total_workers=50,
                 threads_per_provider=1):
        self.flat_keys = flat_keys
        self.key_manager = key_manager
        self.job_queue = job_queue  # asyncio.Queue from main loop
        self.results_dict = results_dict
        self.target_lang = target_lang
        self.default_model = default_model
        self.character_context = character_context
        self.stream_queue = stream_queue  # asyncio.Queue from main loop
        self.total_jobs = total_jobs
        self.max_total_workers = max_total_workers
        self.threads_per_provider = threads_per_provider
        
        # Group keys by provider
        self.provider_groups = self._group_keys_by_provider()
        
        # Calculate optimal distribution
        self.thread_configs = self._calculate_distribution()
        
    def _group_keys_by_provider(self):
        """Group flat_keys by provider name."""
        groups = {}
        for fk in self.flat_keys:
            # Extract provider from account_name (e.g., "Gemini_1" -> "Gemini")
            provider = fk['account_name'].split('_')[0].lower()
            if provider not in groups:
                groups[provider] = []
            groups[provider].append(fk)
        return groups
    
    def _calculate_distribution(self):
        """Calculate optimal threads and workers per thread.
        
        Strategy (RPM-based):
        - workers_needed = ceil(RPM / 60 * avg_latency_sec)
        - threads = ceil(workers / workers_per_thread)
        - Each thread gets its own httpx pool + event loop
        """
        AVG_LATENCY = 3  # Average API response time in seconds
        MAX_WORKERS_PER_THREAD = 15  # httpx pool per thread
        configs = []
        remaining_budget = self.max_total_workers
        
        # Feature 2: support threads_per_provider (split single provider across N threads)
        threads_per_prov = getattr(self, 'threads_per_provider', 1)
        
        # Sort providers by total RPM (highest first)
        provider_rpm = []
        for prov, keys in self.provider_groups.items():
            total_rpm = 0
            for fk in keys:
                acc = fk['account_name']
                limiter = self.key_manager.limiters.get(acc)
                if limiter:
                    total_rpm += limiter.rpm
                else:
                    total_rpm += 30  # default low RPM
            provider_rpm.append((prov, keys, total_rpm))
        
        provider_rpm.sort(key=lambda x: x[2], reverse=True)
        
        for prov, keys, total_rpm in provider_rpm:
            if remaining_budget <= 0:
                break
            
            num_keys = len(keys)
            
            # RPM-based: how many workers needed to saturate this provider's RPM?
            # workers = ceil(RPM / 60 * latency) → e.g., 2000/60 * 3 = 100
            rpm_based_workers = max(int(total_rpm / 60 * AVG_LATENCY + 0.5), num_keys)
            
            # Also consider: at least 1 worker per key, at most remaining budget
            ideal_workers = min(rpm_based_workers, remaining_budget, self.total_jobs)
            ideal_workers = max(ideal_workers, 1)
            
            # How many threads to split across?
            # User override or auto-calculate based on workers per thread cap
            if threads_per_prov > 1:
                actual_threads = min(threads_per_prov, ideal_workers)
            else:
                # Auto: 1 thread per ~MAX_WORKERS_PER_THREAD workers
                actual_threads = max(1, (ideal_workers + MAX_WORKERS_PER_THREAD - 1) // MAX_WORKERS_PER_THREAD)
                # Note: single key CAN span multiple threads (key_manager is thread-safe)
            
            if actual_threads > 1:
                # Split keys across sub-threads
                # If fewer keys than threads, replicate keys (all threads share same keys)
                if num_keys >= actual_threads:
                    keys_per_thread = [keys[i::actual_threads] for i in range(actual_threads)]
                else:
                    # Single key high-RPM: all sub-threads share the same key(s)
                    keys_per_thread = [keys[:] for _ in range(actual_threads)]
                
                workers_per_thread = max(ideal_workers // actual_threads, 1)
                for t_idx in range(actual_threads):
                    t_keys = keys_per_thread[t_idx]
                    w = min(workers_per_thread, remaining_budget)
                    if w <= 0:
                        break
                    configs.append({
                        'provider': f"{prov}_{t_idx+1}",
                        'keys': t_keys,
                        'num_workers': w,
                        'total_rpm': total_rpm // actual_threads,
                    })
                    remaining_budget -= w
            else:
                workers = min(ideal_workers, remaining_budget)
                workers = max(workers, 1)
                configs.append({
                    'provider': prov,
                    'keys': keys,
                    'num_workers': workers,
                    'total_rpm': total_rpm,
                })
                remaining_budget -= workers
        
        # Log distribution
        for c in configs:
            print(f"  Thread[{c['provider']}]: {len(c['keys'])} keys × {c['num_workers']} workers (RPM: {c['total_rpm']})")
        
        return configs
    
    def get_distribution_info(self):
        """Return human-readable distribution info."""
        total_workers = sum(c['num_workers'] for c in self.thread_configs)
        lines = [f"Thread Distribution: {len(self.thread_configs)} threads, {total_workers} total workers"]
        for cfg in self.thread_configs:
            lines.append(f"  Thread [{cfg['provider'].upper()}]: "
                        f"{len(cfg['keys'])} keys × {cfg['num_workers']} workers "
                        f"(~{cfg['total_rpm']} RPM)")
        return '\n'.join(lines)
    
    async def run(self):
        """Launch all threads and wait for completion.
        
        Each thread runs its own event loop with M async workers.
        The main event loop bridges results via thread-safe mechanisms.
        """
        main_loop = asyncio.get_event_loop()
        total_workers = sum(c['num_workers'] for c in self.thread_configs)
        
        print(f"\n{'='*60}")
        print(f"THREADED WORKER POOL — MULTI-THREAD MODE")
        print(self.get_distribution_info())
        print(f"{'='*60}\n")
        
        # Thread-safe done counter
        threads_done = threading.Event()
        active_threads = {'count': len(self.thread_configs)}
        count_lock = threading.Lock()
        
        # Bridge: thread-safe queue for results → main event loop
        result_bridge = thread_queue.Queue()
        
        def thread_worker(config):
            """Each thread runs its own event loop with async workers."""
            prov = config['provider']
            num_w = config['num_workers']
            
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(
                    self._run_thread_workers(config, result_bridge, main_loop)
                )
            except Exception as e:
                print(f"[Thread-{prov}] Error: {e}")
            finally:
                loop.close()
                with count_lock:
                    active_threads['count'] -= 1
                    if active_threads['count'] == 0:
                        threads_done.set()
                        # Signal main loop that all threads are done
                        result_bridge.put({'type': 'all_threads_done'})
                print(f"[Thread-{prov}] Finished ({num_w} workers)")
        
        # Add sentinels for ALL workers across all threads
        for cfg in self.thread_configs:
            for _ in range(cfg['num_workers']):
                await self.job_queue.put(None)
        
        # Launch threads
        executor = ThreadPoolExecutor(
            max_workers=len(self.thread_configs),
            thread_name_prefix='TranslatorThread'
        )
        
        for cfg in self.thread_configs:
            executor.submit(thread_worker, cfg)
        
        # Bridge loop: forward results from threads → main event loop's stream_queue
        while True:
            # Non-blocking poll of thread results
            try:
                msg = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: result_bridge.get(timeout=0.5)
                )
                if msg['type'] == 'all_threads_done':
                    break
                elif msg['type'] == 'chunk':
                    await self.stream_queue.put(msg)
            except thread_queue.Empty:
                continue
        
        executor.shutdown(wait=False)
    
    async def _run_thread_workers(self, config, result_bridge, main_loop):
        """Run M async workers within one thread's event loop."""
        prov = config['provider']
        num_w = config['num_workers']
        
        # Each thread gets its own httpx client
        client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=15)
        )
        
        workers = []
        for i in range(num_w):
            w = AIWorker(
                worker_id=f"{prov}-{i+1}",
                key_manager=self.key_manager,
                target_lang=self.target_lang,
                default_model=self.default_model,
                character_context=self.character_context,
                stream_queue=None,  # Don't send directly; use bridge
                shared_client=client
            )
            workers.append(w)
        
        # Override stream_queue to use bridge
        bridge_queue = asyncio.Queue()
        for w in workers:
            w.stream_queue = bridge_queue
        
        # Run workers + bridge forwarder concurrently
        async def bridge_forwarder():
            """Forward results from this thread's async queue to thread-safe bridge."""
            workers_remaining = num_w
            while workers_remaining > 0:
                msg = await bridge_queue.get()
                if msg.get('type') == 'worker_exit':
                    workers_remaining -= 1
                    continue
                result_bridge.put(msg)
        
        # Patch workers to signal exit
        original_process = AIWorker.process_jobs
        
        async def patched_process(worker_self, jq, rd):
            await original_process(worker_self, jq, rd)
            await bridge_queue.put({'type': 'worker_exit'})
        
        tasks = []
        for i, w in enumerate(workers):
            # Stagger worker starts: delay = min(i × 0.4s, 2.0s max)
            # Prevents burst regardless of worker count (15 workers = still max 2s spread)
            stagger_delay = min(i * 0.4, 2.0)
            async def make_staggered(worker=w, delay=stagger_delay):
                if delay > 0:
                    await asyncio.sleep(delay)
                await patched_process(worker, self.job_queue, self.results_dict)
            tasks.append(asyncio.create_task(make_staggered()))

        tasks.append(asyncio.create_task(bridge_forwarder()))
        
        await asyncio.gather(*tasks)
        await client.aclose()


async def run_translation(file_path: str, target_lang: str, custom_keys: list = None, batch_size: int = 30, model_name: str = None, cancel_event: asyncio.Event = None, key_manager_holder: dict = None):
    # Chuyen ma ngon ngu ngan thanh ten day du
    full_lang = LANG_MAP.get(target_lang, target_lang)
    used_model = model_name or MODEL_NAME
    print(f"Bat dau quy trinh dich file {file_path} sang {full_lang} voi batch_size={batch_size}, model={used_model}")
    
    # Buoc 1: Parse SRT & Tao Jobs
    file_id = file_path
    subtitles = SubtitleChunker.parse_srt(file_path)
    
    # Buoc 0.5: Pre-process than tu Trung -> Viet (truoc khi gui AI)
    excl_count = 0
    # Translation dedup cache: source_text -> translated_text
    _trans_cache = {}
    
    for sub in subtitles:
        new_text = replace_exclamation(sub['text'])
        if new_text != sub['text']:
            excl_count += 1
            sub['text'] = new_text
    if excl_count > 0:
        print(f"--- Da Viet hoa {excl_count} than tu truoc khi dich.")
    
    # Auto batch_size theo provider (nếu user dùng default 30)
    if batch_size == 30:
        name_lower = used_model.lower()
        if name_lower.startswith('gemini'):
            batch_size = 100  # Gemini: 4M TPM, 1M context — batch lớn giảm overhead
        elif name_lower.startswith('gpt'):
            batch_size = 40  # OpenAI: 200K TPM
        elif name_lower.startswith('deepseek'):
            batch_size = 35  # DeepSeek: 100K TPM
        # Groq: giữ 30 (TPM thấp 6000)
        print(f"--- Auto batch_size: {batch_size} (toi uu cho {used_model})")
    
    # Chia file thanh nhieu chunk, moi chunk max batch_size dong
    jobs = SubtitleChunker.chunk_subtitles(file_id, subtitles, max_lines=batch_size)
    
    job_queue = JobQueue()
    for job in jobs:
        await job_queue.put(job)
        
    print(f"Da tao {len(jobs)} jobs (chunks) cho file tai len.")

    # Buoc 2: Khoi tao AccountRateLimiter
    limiters = {}
    for acc_name, limits in RATE_LIMITS.items():
        limiters[acc_name] = AccountRateLimiter(rpm=limits['rpm'], tpm=limits['tpm'])

    # Buoc 3: Tao danh sach workers ket hop account/key
    flat_keys = []
    
    if custom_keys and len(custom_keys) > 0:
        # Parse tagged keys: auto-detect Groq/Gemini/OpenAI tu prefix hoac tag
        print(f"\n--- Parsing {len(custom_keys)} custom keys (auto-detect provider)...")
        parsed_keys = parse_tagged_keys(custom_keys, used_model)
        
        for i, pk in enumerate(parsed_keys):
            acc_name = f"{pk['provider'].capitalize()}_{i + 1}"
            flat_keys.append({
                "account_name": acc_name,
                "key": pk['key'],
                "model": pk['model'],
                "endpoint": pk['endpoint'],
            })
            
            # Moi key duoc 1 limiter rieng voi rate limit phu hop
            if acc_name not in limiters:
                limiters[acc_name] = AccountRateLimiter(rpm=pk['rpm'], tpm=pk['tpm'])
    else:
        # Load tu config.py
        for acc in ACCOUNTS:
            for key in acc['keys']:
                flat_keys.append({"account_name": acc['name'], "key": key})
            
    # Buoc 4: Tao APIKeyManager
    key_manager = APIKeyManager(flat_keys, limiters)
    
    # Register for health dashboard
    _active_key_managers[file_path] = key_manager
    
    # Setup pause/resume events
    key_manager.resume_event = asyncio.Event()
    key_manager.pause_event = asyncio.Event()

    # Buoc 0: Phan tich nhan vat (chi khi file >= 30 dong)
    character_context = ""
    if len(subtitles) >= 30:
        print(f"\n--- Buoc 0: Dang phan tich nhan vat va quy tac xung ho...")
        character_context = await analyze_characters(subtitles, key_manager, used_model, full_lang)
    else:
        print(f"--- Bo qua phan tich nhan vat (file chi co {len(subtitles)} dong < 30)")

    if len(flat_keys) == 0:
        print("Loi: Khong tim thay API keys nao duoc thiet lap trong config.py!")
        return
    
    results_dict = {}
    
    stream_queue = asyncio.Queue()
    
    # Store key_manager reference for pause/resume (no yield needed)
    if key_manager_holder is not None:
        key_manager_holder['ref'] = key_manager
    
    # ── ALWAYS use ThreadedWorkerPool for optimal throughput ──
    # (even single provider benefits from multiple threads with high RPM)
    use_threaded = True  # Always thread — distribution handles it smartly
    
    if use_threaded:
        # Multiple providers → use threaded pool
        pool = ThreadedWorkerPool(
            flat_keys=flat_keys,
            key_manager=key_manager,
            job_queue=job_queue,
            results_dict=results_dict,
            target_lang=full_lang,
            default_model=used_model,
            character_context=character_context,
            stream_queue=stream_queue,
            total_jobs=len(jobs),
            max_total_workers=TOTAL_WORKERS
        )
        total_w = sum(c['num_workers'] for c in pool.thread_configs)
        worker_tasks = [asyncio.create_task(pool.run())]
    else:
        # Single provider → classic single-thread mode (simpler, less overhead)
        num_workers = min(len(flat_keys) * 3, len(jobs), TOTAL_WORKERS)
        num_workers = max(num_workers, 1)
        total_w = num_workers
        print(f"Khoi tao {num_workers} worker (single-thread mode) cho {len(flat_keys)} keys va {len(jobs)} chunks...")
        
        shared_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
        )
        
        workers = []
        for i in range(num_workers):
            worker = AIWorker(worker_id=i+1, key_manager=key_manager, target_lang=full_lang, default_model=used_model, character_context=character_context, stream_queue=stream_queue, shared_client=shared_client)
            workers.append(worker)
        
        for _ in range(num_workers):
            await job_queue.put(None)
        
        worker_tasks = [asyncio.create_task(w.process_jobs(job_queue, results_dict)) for w in workers]
    
    workers_done = asyncio.Event()
    
    async def worker_watcher():
        await asyncio.gather(*worker_tasks)
        if not use_threaded:
            await shared_client.aclose()  # Dong shared client sau khi xong
        workers_done.set()
        await stream_queue.put({"type": "workers_done"})
        
    async def health_check_emitter():
        """Gui bao cao suc khoe API moi 10 giay."""
        while not workers_done.is_set():
            await asyncio.sleep(10)
            if not workers_done.is_set():
                report = key_manager.get_health_report()
                await stream_queue.put({"type": "health_check", "data": report})
        # Gui bao cao cuoi cung
        final_report = key_manager.get_health_report()
        await stream_queue.put({"type": "health_check", "data": final_report})
        
    asyncio.create_task(worker_watcher())
    asyncio.create_task(health_check_emitter())
    
    # ── ETA TRACKING ──
    total_chunks = len(jobs)
    chunks_done = 0
    trans_start_time = time.time()
    
    def build_progress():
        elapsed = time.time() - trans_start_time
        remaining = total_chunks - chunks_done
        avg = elapsed / max(chunks_done, 1)
        eta = avg * remaining if chunks_done > 0 else 0
        speed = round(chunks_done / max(elapsed / 60, 0.01), 1)
        return {
            "done": chunks_done,
            "total": total_chunks,
            "elapsed_sec": round(elapsed, 1),
            "eta_sec": round(eta, 1),
            "speed": speed,  # chunks/min
            "percent": round(chunks_done / max(total_chunks, 1) * 100, 1)
        }
    
    # Yield initial progress
    yield {"type": "progress", "progress": build_progress()}
    
    # Lang nghe ket qua tra ve tu Queue
    while True:
        # Check cancel
        if cancel_event and cancel_event.is_set():
            print("--- Huy dich theo yeu cau nguoi dung.")
            yield {"type": "cancelled", "progress": build_progress()}
            # Cancel all workers
            for t in worker_tasks:
                t.cancel()
            return
        
        msg = await stream_queue.get()
        if msg["type"] == "workers_done":
            break
        elif msg["type"] == "health_check":
            msg["progress"] = build_progress()
            yield msg
        elif msg["type"] == "chunk":
            chunks_done += 1
            # Xu ly parse string thanh JSON array de gui cho frontend hien thi muot
            chunk_res = msg["data"]
            job = chunk_res['job']
            subs_ref = job['subs_ref']
            translated_str = chunk_res['translated_texts']
            
            # Use centralized parser (fixes multi-line, duplicate [N], AI notes)
            trans_texts = parse_translated_response(translated_str, subs_ref)
                    
            parsed_chunck_data = []
            for i, sub in enumerate(subs_ref):
                parsed_chunck_data.append({
                    "id": sub['id'],
                    "start": sub['start'],
                    "end": sub['end'],
                    "text": trans_texts[i]
                })
                
            yield {"type": "chunk", "data": parsed_chunck_data, "progress": build_progress()}

    # Buoc 5: Reconstruct ket qua -> Luu file
    if file_id not in results_dict:
        print("Khong co ket qua nao duoc sinh ra.")
        yield {"type": "error", "message": "No output generated"}
        return
        
    file_results = results_dict[file_id]
    sorted_chunks = [file_results[i] for i in sorted(file_results.keys())]
    
    final_srt_lines = []
    json_output_data = []
    
    for chunk_res in sorted_chunks:
        job = chunk_res['job']
        subs_ref = job['subs_ref']
        
        translated_str = chunk_res['translated_texts']
        
        # Use centralized parser (fixes multi-line, duplicate [N], AI notes)
        trans_texts = parse_translated_response(translated_str, subs_ref)

        # Lap ghep lai theo format SRT chuan
        for i, sub in enumerate(subs_ref):
            t_text = trans_texts[i]
            
            # Populate dedup cache
            src_text = sub['text'].strip()
            if t_text and t_text.strip() and src_text:
                _trans_cache[src_text] = t_text
            
            # 1. Them vao SRT
            final_srt_lines.append(f"{sub['id']}\n{sub['start']} --> {sub['end']}\n{t_text}\n")
            
            # 2. Them vao JSON Optional
            json_output_data.append({
                "id": sub['id'],
                "start": sub['start'],
                "end": sub['end'],
                "original_text": sub['text'],
                "translated_text": t_text
            })

    # Dedup fill: fill untranslated lines from cache
    dedup_filled = 0
    for item in json_output_data:
        if not item['translated_text'] or not item['translated_text'].strip():
            cached = _trans_cache.get(item['original_text'].strip())
            if cached:
                item['translated_text'] = cached
                dedup_filled += 1
    if dedup_filled > 0:
        print(f"--- Dedup cache: filled {dedup_filled} duplicate lines from cache.")

    print(f"\n--- Dich xong! Dang kiem tra cac dong con sot ky tu nguon...")
    
    # Buoc 6: Auto-cleanup - Tim va dich lai cac dong con chua ky tu nguon
    dirty_lines = []
    for i, item in enumerate(json_output_data):
        if has_source_chars(item['translated_text'], full_lang):
            dirty_lines.append((i, item['original_text'], item['translated_text']))
    
    if dirty_lines:
        print(f"!! Tim thay {len(dirty_lines)} dong con ky tu nguon. Dang dich lai song song...")
        
        # Them character context vao cleanup prompt neu co
        char_ref = ""
        if character_context:
            char_ref = f"\n\nCharacter reference for names:\n{character_context[:800]}\n"
        
        cleanup_prompt = f"""Translate the following text COMPLETELY to {full_lang}.{char_ref}
CRITICAL RULES:
- Output ONLY the translation, nothing else.
- EVERY character must be in {full_lang}. ABSOLUTELY ZERO Chinese/Japanese/Korean characters allowed.
- Transliterate all names phonetically to {full_lang}.
- Translate ALL particles and suffixes (呢→nhỉ/nha, 吧→đi/nhé, 呀→à, 啊→a/à, 吗→sao/không, 的→của)."""
        
        sem = asyncio.Semaphore(5)  # 5 requests song song
        
        async def cleanup_one_line(idx, orig_text, bad_text, client, max_retries=2):
            async with sem:
                for attempt in range(max_retries):
                    try:
                        acc_name, api_key, _, _ = await key_manager.get_available_key(len(orig_text) // 4 + 50)
                    except:
                        continue
                    
                    payload = {
                        "model": used_model,
                        "messages": [
                            {"role": "system", "content": cleanup_prompt},
                            {"role": "user", "content": orig_text}
                        ],
                        "stream": False,
                        "temperature": 0.1,
                        "max_tokens": 512
                    }
                    endpoint = get_endpoint(used_model)
                    
                    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                    
                    try:
                        resp = await client.post(endpoint, json=payload, headers=headers)
                        if resp.status_code == 200:
                            fixed = resp.json()['choices'][0]['message']['content'].strip()
                            fixed = re.sub(r'^\[\d+\]\s*', '', fixed)
                            # Loai bo dau ngoac kep boc neu AI tra "text"
                            fixed = fixed.strip('"').strip("'")
                            
                            if not has_source_chars(fixed, full_lang):
                                json_output_data[idx]['translated_text'] = fixed
                                final_srt_lines[idx] = f"{json_output_data[idx]['id']}\n{json_output_data[idx]['start']} --> {json_output_data[idx]['end']}\n{fixed}\n"
                                print(f"  --- Dong {idx+1}: '{bad_text[:25]}' -> '{fixed[:25]}'")
                                
                                # Day su kien qua stream cho Frontend
                                await stream_queue.put({"type": "cleanup_chunk", "data": [{"id": json_output_data[idx]['id'], "text": fixed}]})
                                return True
                            # Neu van con CJK, thu lai
                        elif resp.status_code == 429:
                            await key_manager.mark_sleep(api_key, 8)
                    except:
                        pass
                
                # Sau max_retries ma van loi: dung regex strip CJK chars cuoi cung
                stripped = re.sub(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+', '', bad_text).strip()
                if stripped:
                    json_output_data[idx]['translated_text'] = stripped
                    final_srt_lines[idx] = f"{json_output_data[idx]['id']}\n{json_output_data[idx]['start']} --> {json_output_data[idx]['end']}\n{stripped}\n"
                    print(f"  --- Dong {idx+1}: Regex stripped CJK -> '{stripped[:30]}'")
                return False
        
        async def cleanup_watcher():
            async with httpx.AsyncClient(timeout=60.0) as client:
                tasks = [cleanup_one_line(idx, orig, bad, client) for idx, orig, bad in dirty_lines]
                results = await asyncio.gather(*tasks)
                fixed_count = sum(1 for r in results if r)
                print(f"--- Cleanup hoan tat: {fixed_count}/{len(dirty_lines)} dong sua thanh cong.")
                await stream_queue.put({"type": "cleanup_done"})
            
        asyncio.create_task(cleanup_watcher())
        
        while True:
            msg = await stream_queue.get()
            if msg["type"] == "cleanup_done":
                break
            elif msg["type"] == "cleanup_chunk":
                yield msg
                
    else:
        print(f"--- Khong co dong nao con sot ky tu nguon. Tuyet voi!")

    # Ghi file cuoi cung (sau cleanup)
    out_srt_file = file_path.replace(".srt", f"_{target_lang}.srt")
    with open(out_srt_file, "w", encoding="utf-8") as f:
        f.write("\n".join(final_srt_lines).strip() + "\n")
        
    out_json_file = file_path.replace(".srt", f"_{target_lang}.json")
    with open(out_json_file, "w", encoding="utf-8") as f:
        json.dump(json_output_data, f, ensure_ascii=False, indent=2)
        
    print(f"\n--- Hoan tat! Files luu tai:\n- {out_srt_file}\n- {out_json_file}")
    
    # Báo hiệu Done và gửi lại kết quả final string qua stream
    yield {"type": "done", "translated_srt_url": out_srt_file}

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Cu phap: python translator.py <file.srt> <ngon_ngu_dich>")
        sys.exit(1)
        
    srt_path = sys.argv[1]
    lang = sys.argv[2]
    
    asyncio.run(run_translation(srt_path, lang))
