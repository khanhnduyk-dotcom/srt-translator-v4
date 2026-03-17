from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
import json
import shutil
import os
import time
import sys
import asyncio
import base64
import tempfile
import logging
import glob
import random
import re
from contextlib import asynccontextmanager

# Set UTF-8 encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    
# Ensure current directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from translator import run_translation


# ── Custom exceptions ──
class CookieDeadError(Exception):
    """Raised when a cookie is expired/invalid — triggers hot-swap to next cookie in pool."""
    def __init__(self, cookie_id: str, reason: str):
        self.cookie_id = cookie_id
        self.reason = reason
        super().__init__(f"Cookie {cookie_id} dead: {reason}")


TEMP_DIR = "temp_uploads"
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Structured logging ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('server.log', encoding='utf-8')
    ]
)
logger = logging.getLogger('srt_server')

# ── Track active WebSocket sessions for cancel ──
active_sessions = {}


# ── Temp file cleanup task ──
async def cleanup_temp_files():
    """Remove temp files older than 1 hour, every 30 minutes."""
    while True:
        await asyncio.sleep(1800)  # 30 min
        try:
            now = time.time()
            for f in glob.glob(os.path.join(TEMP_DIR, '*')):
                if os.path.isfile(f) and now - os.path.getmtime(f) > 3600:
                    os.remove(f)
                    logger.info(f'Cleanup: removed {f}')
        except Exception as e:
            logger.warning(f'Cleanup error: {e}')

# ── Watchfolder directories ──
WATCH_INPUT = "watch_input"
WATCH_OUTPUT = "watch_output"
WATCH_DONE = "watch_done"
for d in [WATCH_INPUT, WATCH_OUTPUT, WATCH_DONE]:
    os.makedirs(d, exist_ok=True)

watchfolder_status = {"enabled": True, "processing": None, "completed": 0, "errors": 0}

async def watchfolder_scanner():
    """Scan watch_input/ every 5s for .srt files and auto-translate."""
    while True:
        await asyncio.sleep(5)
        if not watchfolder_status["enabled"]:
            continue
        try:
            srt_files = sorted(glob.glob(os.path.join(WATCH_INPUT, '*.srt')))
            if not srt_files or watchfolder_status["processing"]:
                continue
            
            input_path = srt_files[0]
            fname = os.path.basename(input_path)
            watchfolder_status["processing"] = fname
            logger.info(f'[Watchfolder] Processing: {fname}')
            
            try:
                # Load keys from config
                from config import ACCOUNTS
                keys_list = []
                for acc in ACCOUNTS:
                    for k in acc.get("keys", []):
                        if k and k not in ("YOUR_GEMINI_API_KEY_HERE", "key_a1", "key_b1"):
                            keys_list.append(k)
                
                if not keys_list:
                    logger.warning('[Watchfolder] No API keys configured!')
                    watchfolder_status["processing"] = None
                    continue
                
                # Run translation
                async for event in run_translation(input_path, "vi", keys_list, 100, ""):
                    if event.get("type") == "done":
                        break
                
                # run_translation saves output next to input file
                # Move translated file to watch_output/
                import shutil as sh2
                translated_path = input_path.replace('.srt', '_vi.srt')
                output_path = os.path.join(WATCH_OUTPUT, os.path.basename(translated_path))
                done_path = os.path.join(WATCH_DONE, fname)
                
                if os.path.exists(translated_path):
                    sh2.move(translated_path, output_path)
                # Also move JSON if exists
                json_path = input_path.replace('.srt', '_vi.json')
                if os.path.exists(json_path):
                    sh2.move(json_path, os.path.join(WATCH_OUTPUT, os.path.basename(json_path)))
                # Move original to done
                if os.path.exists(input_path):
                    sh2.move(input_path, done_path)
                
                watchfolder_status["completed"] += 1
                logger.info(f'[Watchfolder] Done: {fname} -> {output_path}')
                
            except Exception as e:
                watchfolder_status["errors"] += 1
                logger.error(f'[Watchfolder] Error processing {fname}: {e}')
                # Move to done to avoid retry loop
                try:
                    import shutil as sh2
                    sh2.move(input_path, os.path.join(WATCH_DONE, f"ERROR_{fname}"))
                except:
                    pass
            finally:
                watchfolder_status["processing"] = None
                
        except Exception as e:
            logger.warning(f'[Watchfolder] Scanner error: {e}')


async def cookie_keepalive():
    """Background task: ping Gemini every 10min per cookie to prevent idle expiry.
    On 401 → auto-try CDP refresh from Chrome. Runs forever."""
    import httpx, re, random
    PING_INTERVAL = 600  # 10 minutes
    
    await asyncio.sleep(30)  # Wait for server to fully start
    logger.info("[KeepAlive] Cookie keep-alive started")
    
    while True:
        try:
            if cookie_pool.count() > 0:
                for ck in list(cookie_pool.cookies):
                    if ck.get("blocked_until", 0) > time.time():
                        continue  # Skip blocked cookies
                    
                    cookie_id = ck["id"]
                    try:
                        headers = {
                            "Cookie": ck["cookie"],
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
                            "Referer": "https://gemini.google.com/",
                            "DNT": "1",
                        }
                        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                            page = await client.get("https://gemini.google.com/app", headers=headers)
                        
                        if page.status_code == 200:
                            m = re.search(r'"SNlM0e":"(.*?)"', page.text)
                            if m:
                                ck["snlm0e"] = m.group(1)
                                ck["snlm0e_time"] = time.time()
                                logger.info(f"[KeepAlive] {cookie_id}: ✅ còn sống, SNlM0e refreshed")
                            elif "accounts.google" in str(page.url) or "ServiceLogin" in page.text:
                                logger.warning(f"[KeepAlive] {cookie_id}: ❌ hết hạn! Thử CDP refresh...")
                                # Auto-try CDP refresh
                                try:
                                    cookie_str, info = await _grab_cookies_from_port(9222)
                                    if not cookie_str:
                                        cookie_str, info = await _grab_cookies_from_port(9223)
                                    if cookie_str:
                                        ck["cookie"] = cookie_str
                                        ck["snlm0e"] = None
                                        ck["snlm0e_time"] = 0
                                        cookie_pool._save()
                                        logger.info(f"[KeepAlive] {cookie_id}: 🔄 Auto-refreshed từ Chrome!")
                                    else:
                                        logger.warning(f"[KeepAlive] {cookie_id}: Chrome không mở port 9222/9223")
                                except Exception as e:
                                    logger.warning(f"[KeepAlive] CDP refresh error: {e}")
                        
                        await asyncio.sleep(random.uniform(5, 15))  # Stagger between cookies
                    except Exception as e:
                        logger.debug(f"[KeepAlive] {cookie_id} ping error: {e}")
        except Exception as e:
            logger.warning(f"[KeepAlive] Error: {e}")
        
        await asyncio.sleep(PING_INTERVAL)


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(cleanup_temp_files())
    wf_task = asyncio.create_task(watchfolder_scanner())
    ka_task = asyncio.create_task(cookie_keepalive())
    yield
    task.cancel()
    wf_task.cancel()
    ka_task.cancel()

app = FastAPI(lifespan=lifespan)

# Cấp quyền CORS để gọi từ frontend HTML local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.websocket("/ws/translate")
async def websocket_translate(ws: WebSocket):
    await ws.accept()
    session_id = f"ws_{int(time.time() * 1000)}"
    cancel_event = asyncio.Event()
    active_sessions[session_id] = cancel_event
    
    try:
        # 1. Nhận config từ client (JSON)
        raw = await ws.receive_text()
        config = json.loads(raw)
        
        # 2. Nhận file SRT (base64)
        file_data_b64 = config.get("file_data", "")
        file_name = config.get("file_name", "input.srt")
        target_lang = config.get("target_lang", "vi")
        batch_size = config.get("batch_size", 30)
        model_name = config.get("model_name", "")
        api_keys = config.get("api_keys", [])
        
        if not file_data_b64:
            await ws.send_json({"type": "error", "message": "No file data received"})
            return
            
        # 3. Decode và lưu file
        file_bytes = base64.b64decode(file_data_b64)
        file_id = f"{int(time.time())}_{file_name}"
        input_path = os.path.join(TEMP_DIR, file_id)
        
        with open(input_path, "wb") as f:
            f.write(file_bytes)
        
        await ws.send_json({"type": "started", "session_id": session_id, "file": file_name})
        
        # 4. Parse keys
        keys_list = [k.strip() for k in api_keys if k.strip()] if isinstance(api_keys, list) else [k.strip() for k in api_keys.split('\n') if k.strip()]
        
        # Cookie/Hybrid pool expansion: expand each "cookie" key to N workers
        # Hybrid: ["cookie", "AIza1", "AIza2"] → ["cookie","cookie","AIza1","AIza2"] (if pool=2)
        has_cookie = "cookie" in keys_list
        if has_cookie and cookie_pool.count() > 0:
            pool_size = cookie_pool.count()
            non_cookie_keys = [k for k in keys_list if k != "cookie"]
            expanded_cookies = ["cookie"] * pool_size
            keys_list = expanded_cookies + non_cookie_keys
            mode_label = "Hybrid" if non_cookie_keys else "Cookie-only"
            print(f"[WS] {mode_label}: {pool_size} cookie + {len(non_cookie_keys)} API key workers = {len(keys_list)} total")
        
        # 5. Listener cho client commands (cancel/pause/resume)
        key_manager_ref = {'ref': None}  # Will be set when translation starts
        
        async def listen_commands():
            try:
                while not cancel_event.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.receive_text(), timeout=0.5)
                        cmd = json.loads(msg) if msg.startswith('{') else {"action": msg}
                        action = cmd.get("action", "")
                        
                        if action == "cancel":
                            cancel_event.set()
                            print(f"[WS] Session {session_id}: Cancel requested")
                            break
                        elif action == "resume":
                            # Hot-inject new keys and resume
                            new_keys = cmd.get("new_keys", [])
                            new_model = cmd.get("new_model", "")
                            km = key_manager_ref.get('ref')
                            if km and new_keys:
                                from translator import parse_tagged_keys, AccountRateLimiter
                                parsed = parse_tagged_keys(new_keys, new_model or "")
                                new_flat = []
                                new_limiters = {}
                                for i, pk in enumerate(parsed):
                                    acc = f"{pk['provider'].capitalize()}_resume_{i+1}"
                                    new_flat.append({
                                        "account_name": acc,
                                        "key": pk['key'],
                                        "model": pk['model'],
                                        "endpoint": pk['endpoint'],
                                    })
                                    new_limiters[acc] = AccountRateLimiter(rpm=pk['rpm'], tpm=pk['tpm'])
                                await km.add_keys(new_flat, new_limiters)
                                if km.resume_event:
                                    km.resume_event.set()
                                print(f"[WS] Session {session_id}: Resumed with {len(new_keys)} new keys")
                                await ws.send_json({"type": "resumed", "keys_added": len(new_keys)})
                    except asyncio.TimeoutError:
                        continue
                    except WebSocketDisconnect:
                        cancel_event.set()
                        break
            except Exception:
                pass
        
        # Start command listener + heartbeat in background
        cmd_task = asyncio.create_task(listen_commands())
        
        # WS heartbeat: ping every 30s to keep connection alive
        async def heartbeat():
            try:
                while not cancel_event.is_set():
                    await asyncio.sleep(30)
                    try:
                        await ws.send_json({"type": "ping"})
                    except:
                        break
            except:
                pass
        hb_task = asyncio.create_task(heartbeat())
        
        # 6. Chạy translation và stream kết quả
        try:
            async for event in run_translation(input_path, target_lang, keys_list, batch_size, model_name, cancel_event=cancel_event, key_manager_holder=key_manager_ref):
                if cancel_event.is_set():
                    break
                try:
                    await ws.send_json(event)
                except Exception:
                    break
        except Exception as e:
            try:
                await ws.send_json({"type": "error", "message": str(e)})
            except:
                pass
        
        cmd_task.cancel()
        hb_task.cancel()
        
        # Cleanup
        try:
            os.remove(input_path)
        except:
            pass
            
    except WebSocketDisconnect:
        cancel_event.set()
        print(f"[WS] Session {session_id}: Client disconnected")
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        active_sessions.pop(session_id, None)
        try:
            await ws.close()
        except:
            pass


# ── SSE ENDPOINT (giữ lại làm fallback) ──
@app.post("/translate")
async def translate_file(
    file: UploadFile = File(...),
    target_lang: str = Form(...),
    api_keys: str = Form(""),
    batch_size: int = Form(30),
    model_name: str = Form("llama-3.3-70b-versatile")
):
    if not file.filename.endswith(".srt"):
        raise HTTPException(status_code=400, detail="Only .srt files are allowed")
        
    # Lưu file srt đầu vào
    file_id = f"{int(time.time())}_{file.filename}"
    input_path = os.path.join(TEMP_DIR, file_id)
    
    with open(input_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    try:
        # Parse keys from input
        keys_list = [k.strip() for k in api_keys.split('\n') if k.strip()]
        
        async def event_generator():
            try:
                # Gọi process translation dưới dạng generator
                async for event in run_translation(input_path, target_lang, keys_list, batch_size, model_name):
                    # Gửi event dưới dạng SSE (Server-Sent Events)
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SINGLE LINE RETRY (accepts OpenAI chat format, proxies to AI API) ──
@app.post("/retry")
async def retry_single_line(request_body: dict):
    """Proxy single-line translation to AI API.
    Accepts OpenAI chat completion format and forwards to the appropriate API.
    """
    import httpx
    
    model = request_body.get("model", "")
    messages = request_body.get("messages", [])
    api_key = request_body.get("api_key", "")
    temperature = request_body.get("temperature", 0.3)
    max_tokens = request_body.get("max_tokens", 512)
    
    # Auto-detect endpoint from model name
    if "gemini" in model.lower():
        endpoint = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    elif any(x in model.lower() for x in ["llama", "mixtral", "whisper"]):
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
    else:
        endpoint = "https://api.groq.com/openai/v1/chat/completions"
    
    # If no API key provided, try to get from config
    if not api_key:
        try:
            from config import ACCOUNTS
            for acc in ACCOUNTS:
                for k in acc.get("keys", []):
                    if k and k not in ("YOUR_GEMINI_API_KEY_HERE", "key_a1", "key_b1"):
                        api_key = k
                        break
                if api_key:
                    break
        except:
            pass
    
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key available for retry")
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        return resp.json()


# ── WATCHFOLDER STATUS ──
@app.get("/watchfolder/status")
async def wf_status():
    pending = len(glob.glob(os.path.join(WATCH_INPUT, '*.srt')))
    return {**watchfolder_status, "pending": pending}


# ── GEMINI COOKIE POOL ──
POOL_FILE = os.path.join(BASE_DIR if 'BASE_DIR' in dir() else os.path.dirname(os.path.abspath(__file__)), "cookie_pool.json")
OLD_COOKIE_FILE = os.path.join(os.path.dirname(POOL_FILE), "cookie_store.json")

class CookiePool:
    """Multi-cookie pool with round-robin rotation."""
    
    def __init__(self):
        self.cookies = []        # [{id, cookie, added, snlm0e, snlm0e_time, blocked_until}]
        self._robin_idx = 0
        self._lock = asyncio.Lock() if asyncio.get_event_loop().is_running() else None
        self._load()
    
    def _load(self):
        """Load pool from disk. Migrate old single-cookie if exists."""
        try:
            if os.path.exists(POOL_FILE):
                with open(POOL_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for c in data.get("cookies", []):
                        self.cookies.append({
                            "id": c["id"], "cookie": c["cookie"],
                            "added": c.get("added", ""), 
                            "snlm0e": None, "snlm0e_time": 0, "blocked_until": 0
                        })
            elif os.path.exists(OLD_COOKIE_FILE):
                # Migrate from old single cookie
                with open(OLD_COOKIE_FILE, 'r', encoding='utf-8') as f:
                    old = json.load(f)
                    if old.get("cookie"):
                        self.cookies.append({
                            "id": "cookie_1", "cookie": old["cookie"],
                            "added": old.get("saved_at", ""),
                            "snlm0e": None, "snlm0e_time": 0, "blocked_until": 0
                        })
        except Exception as e:
            logger.warning(f"[CookiePool] Load error: {e}")
        
        if self.cookies:
            logger.info(f"[CookiePool] Loaded {len(self.cookies)} cookies from disk")
    
    def _save(self):
        """Save pool to disk."""
        try:
            data = {"cookies": [{"id": c["id"], "cookie": c["cookie"], "added": c["added"]} 
                                for c in self.cookies]}
            with open(POOL_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[CookiePool] Save error: {e}")
    
    def _extract_sid(self, cookie_str):
        """Extract __Secure-1PSID value for dedup."""
        for part in cookie_str.split(";"):
            part = part.strip()
            if part.startswith("__Secure-1PSID="):
                return part.split("=", 1)[1][:30]
        return None
    
    def add(self, cookie_str: str, cookie_id: str = None) -> dict:
        """Add a cookie to pool. Returns info."""
        if not cookie_str.strip():
            return {"ok": False, "error": "Cookie rỗng"}
        
        # Auto-generate unique ID
        existing_nums = []
        for c in self.cookies:
            try: existing_nums.append(int(c["id"].split("_")[-1]))
            except: pass
        next_num = max(existing_nums, default=0) + 1
        cookie_id = cookie_id or f"cookie_{next_num}"
        
        # Only block exact duplicate (same full cookie string)
        import hashlib
        new_hash = hashlib.md5(cookie_str.strip().encode()).hexdigest()[:16]
        for c in self.cookies:
            if hashlib.md5(c["cookie"].encode()).hexdigest()[:16] == new_hash:
                return {"ok": False, "error": f"Cookie trùng 100% với {c['id']}"}
        
        self.cookies.append({
            "id": cookie_id, "cookie": cookie_str.strip(),
            "added": time.strftime('%Y-%m-%d %H:%M:%S'),
            "snlm0e": None, "snlm0e_time": 0, "blocked_until": 0
        })
        self._save()
        return {"ok": True, "id": cookie_id, "total": len(self.cookies)}
    
    def remove(self, cookie_id: str) -> bool:
        before = len(self.cookies)
        self.cookies = [c for c in self.cookies if c["id"] != cookie_id]
        if len(self.cookies) < before:
            self._save()
            return True
        return False
    
    def get_next(self) -> dict | None:
        """Round-robin: get next available cookie (skip blocked)."""
        if not self.cookies:
            return None
        
        now = time.time()
        n = len(self.cookies)
        for _ in range(n):
            idx = self._robin_idx % n
            self._robin_idx += 1
            c = self.cookies[idx]
            if c["blocked_until"] <= now:
                return c
        
        # All blocked — return least-blocked
        return min(self.cookies, key=lambda c: c["blocked_until"])
    
    def block(self, cookie_id: str, seconds: int = 60):
        """Block a cookie for N seconds after error."""
        for c in self.cookies:
            if c["id"] == cookie_id:
                c["blocked_until"] = time.time() + seconds
                logger.warning(f"[CookiePool] {cookie_id} blocked for {seconds}s")
                break
    
    def get_any(self) -> str | None:
        """Get any valid cookie string (for backward compat)."""
        c = self.get_next()
        return c["cookie"] if c else None
    
    def count(self) -> int:
        return len(self.cookies)
    
    def status(self) -> list:
        """Return status of all cookies."""
        now = time.time()
        result = []
        for c in self.cookies:
            blocked = c["blocked_until"] > now
            has_sid = "__Secure-1PSID" in c["cookie"] or "SID" in c["cookie"]
            result.append({
                "id": c["id"], "added": c["added"],
                "has_sid": has_sid,
                "chars": len(c["cookie"]),
                "blocked": blocked,
                "blocked_remaining": max(0, int(c["blocked_until"] - now)) if blocked else 0,
                "has_token": c["snlm0e"] is not None and (now - c["snlm0e_time"]) < 300,
            })
        return result


# Initialize pool
cookie_pool = CookiePool()

def get_cookie_string():
    """Get cookie (backward compat): returns any available cookie."""
    return cookie_pool.get_any()


CDP_PORTS = range(9222, 9227)  # Scan ports 9222-9226

async def _grab_cookies_from_port(port):
    """Grab Google cookies from one CDP port. Returns (cookie_str, info) or (None, error)."""
    import urllib.request, websockets as ws_lib
    
    try:
        req = urllib.request.urlopen(f"http://localhost:{port}/json/version", timeout=2)
        version = json.loads(req.read())
        ws_url = version.get("webSocketDebuggerUrl", "")
    except Exception:
        return None, None
    
    if not ws_url:
        return None, None
    
    try:
        async with ws_lib.connect(ws_url) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            result = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    except Exception:
        return None, None
    
    google = {}
    for c in result.get("result", {}).get("cookies", []):
        if "google" in c.get("domain", ""):
            google[c["name"]] = c["value"]
    
    if not google or not any(k in google for k in ["__Secure-1PSID", "__Secure-3PSID", "SID"]):
        return None, None
    
    cookie_str = "; ".join(f"{k}={v}" for k, v in google.items())
    return cookie_str, google


@app.post("/cookie-auto")
async def cookie_auto():
    """Auto-grab cookies from ALL Chrome profiles (scan ports 9222-9226)."""
    found = 0
    added = 0
    updated = 0
    errors = []
    
    for port in CDP_PORTS:
        cookie_str, info = await _grab_cookies_from_port(port)
        if not cookie_str:
            continue
        
        found += 1
        result = cookie_pool.add(cookie_str)
        
        if result["ok"]:
            added += 1
            logger.info(f"[CDP] Port {port}: added {result['id']}")
        elif "trùng" in result.get("error", ""):
            # Update existing
            import hashlib
            new_hash = hashlib.md5(cookie_str.strip().encode()).hexdigest()[:16]
            for c in cookie_pool.cookies:
                if hashlib.md5(c["cookie"].encode()).hexdigest()[:16] == new_hash:
                    c["cookie"] = cookie_str
                    c["snlm0e"] = None
                    cookie_pool._save()
                    updated += 1
                    break
    
    if found == 0:
        return {
            "ok": False,
            "error": "Không tìm thấy Chrome nào có debug port. Chạy open_profiles.bat trước!",
            "pool_size": cookie_pool.count()
        }
    
    total = cookie_pool.count()
    msg_parts = []
    if added: msg_parts.append(f"{added} mới")
    if updated: msg_parts.append(f"{updated} cập nhật")
    msg = f"✅ Tìm {found} profile → {', '.join(msg_parts) or 'đã có'} (pool: {total})"
    
    return {"ok": True, "message": msg, "found": found, "added": added, "pool_size": total}


_keepalive_last_run = 0

@app.post("/cookie-keepalive-now")
async def cookie_keepalive_now():
    """Manually trigger immediate keepalive ping for all cookies."""
    global _keepalive_last_run
    import httpx, re
    results = []
    
    for ck in list(cookie_pool.cookies):
        cookie_id = ck["id"]
        try:
            headers = {
                "Cookie": ck["cookie"],
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
                "Referer": "https://gemini.google.com/",
            }
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                page = await client.get("https://gemini.google.com/app", headers=headers)
            
            if page.status_code == 200:
                m = re.search(r'"SNlM0e":"(.*?)"', page.text)
                alive = bool(m) and "accounts.google" not in str(page.url)
                if m and alive:
                    ck["snlm0e"] = m.group(1)
                    ck["snlm0e_time"] = time.time()
                results.append({"id": cookie_id, "alive": alive, "status": "✅ Còn sống" if alive else "❌ Hết hạn"})
            else:
                results.append({"id": cookie_id, "alive": False, "status": f"HTTP {page.status_code}"})
        except Exception as e:
            results.append({"id": cookie_id, "alive": False, "status": f"Error: {str(e)[:50]}"})
    
    _keepalive_last_run = time.time()
    alive_count = sum(1 for r in results if r["alive"])
    return {"ok": True, "results": results, "alive": alive_count, "total": len(results)}


@app.get("/cookie-pool")
async def get_cookie_pool():
    """List all cookies in pool with status."""
    return {"cookies": cookie_pool.status(), "total": cookie_pool.count()}


@app.post("/cookie-add")
async def cookie_add(body: dict):
    """Add a cookie to pool."""
    cookie_str = body.get("cookie", "").strip()
    cookie_id = body.get("id", "").strip() or None
    result = cookie_pool.add(cookie_str, cookie_id)
    return result


@app.delete("/cookie-remove/{cookie_id}")
async def cookie_remove(cookie_id: str):
    """Remove a cookie from pool."""
    ok = cookie_pool.remove(cookie_id)
    return {"ok": ok, "total": cookie_pool.count()}


@app.get("/cookie-status")
async def cookie_status():
    """Check cookie pool status."""
    if cookie_pool.count() == 0:
        return {"ok": False, "error": "Chưa có cookie. Paste cookie vào pool."}
    
    pool_info = cookie_pool.status()
    valid = sum(1 for c in pool_info if c["has_sid"])
    return {
        "ok": valid > 0,
        "source": "pool",
        "hint": f"✅ {valid}/{cookie_pool.count()} cookie hợp lệ",
        "pool": pool_info,
        "error": None if valid > 0 else "Không có cookie hợp lệ trong pool"
    }


@app.post("/cookie-set")
async def cookie_set(body: dict):
    """Add cookie to pool (backward compat with old single-cookie UI)."""
    cookie_str = body.get("cookie", "").strip()
    if cookie_str:
        result = cookie_pool.add(cookie_str)
        if not result["ok"] and "trùng" in result.get("error", ""):
            return {"ok": True, "message": "Cookie đã có trong pool"}
        return {"ok": True, "message": f"Cookie thêm vào pool ({cookie_pool.count()} total)"}


@app.post("/cookie-translate")
async def cookie_translate(body: dict):
    """Translate via Gemini internal web API (batchexecute) with cookie auth.
    Accepts BOTH formats:
      1. OpenAI: { model, messages: [{role, content}], temperature, max_tokens }
      2. Texts:  { texts: [...], target, prompt, model }
    """
    import httpx, re, urllib.parse
    
    # ── Select cookie from pool (round-robin) ──
    ck = cookie_pool.get_next()
    if not ck:
        raise HTTPException(status_code=401, detail="Không có cookie trong pool. Thêm cookie trước.")
    
    cookie_str = ck["cookie"]
    cookie_id = ck["id"]
    
    # ── Build prompt from input format ──
    messages = body.get("messages", [])
    texts = body.get("texts", [])
    
    if messages:
        parts = [msg.get("content", "") for msg in messages]
        prompt_text = "\n\n".join(parts)
    elif texts:
        target = body.get("target", "vi")
        prompt = body.get("prompt", "")
        numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(texts))
        system_prompt = prompt or f"Dịch {len(texts)} dòng phụ đề sau sang {target}."
        system_prompt += f"\n\nOutput: [1] bản dịch\n[2] ...\nPHẢI hoàn toàn bằng ngôn ngữ đích. Chỉ trả bản dịch, không giải thích."
        prompt_text = f"{system_prompt}\n\n{numbered}"
    else:
        raise HTTPException(status_code=400, detail="No messages or texts provided")
    
    headers = {
        "Cookie": cookie_str,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/app",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "X-Same-Domain": "1",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "DNT": "1",
    }
    
    # ── Hot-swap retry loop: try each cookie in pool until one works ──
    max_attempts = max(cookie_pool.count(), 1)
    last_error = None
    
    try:  # Outer try: catches non-CookieDeadError failures (timeout, network, parse errors)
      for attempt in range(max_attempts):
        # Pick next cookie for this attempt
        _ck = cookie_pool.get_next()
        if not _ck:
            break
        ck = _ck
        cookie_str = ck["cookie"]
        cookie_id = ck["id"]
        # Update headers with current cookie
        headers["Cookie"] = cookie_str
        if attempt > 0:
            logger.info(f"[CookieHotSwap] Retrying with cookie {cookie_id} (attempt {attempt+1}/{max_attempts})")
        
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                # ── Step 1: Get SNlM0e token (per-cookie cache) ──
                snlm0e = ck.get("snlm0e")
                snlm0e_time = ck.get("snlm0e_time", 0)
                
                if not snlm0e or time.time() - snlm0e_time > 300:
                    page = await client.get("https://gemini.google.com/app", headers=headers)
                    if page.status_code in (401, 403):
                        cookie_pool.block(cookie_id, 120)
                        raise CookieDeadError(cookie_id, f"HTTP {page.status_code} fetching SNlM0e")
                    
                    m = re.search(r'"SNlM0e":"(.*?)"', page.text)
                    if not m:
                        if "accounts.google" in str(page.url) or "ServiceLogin" in page.text:
                            cookie_pool.block(cookie_id, 120)
                            raise CookieDeadError(cookie_id, "Session expired — login required")
                        raise CookieDeadError(cookie_id, "SNlM0e token not found in page")
                    
                    snlm0e = m.group(1)
                    ck["snlm0e"] = snlm0e
                    ck["snlm0e_time"] = time.time()
                    logger.info(f"[Cookie:{cookie_id}] Got SNlM0e token")
                
                # ── Step 2: Call batchexecute API (must be inside same client context) ──
                inner = json.dumps([prompt_text, 0, None])
                outer = json.dumps([[inner, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]])
                
                req_data = {
                    "f.req": json.dumps([None, outer]),
                    "at": snlm0e,
                }
                
                await asyncio.sleep(random.uniform(0.3, 0.8))  # Jitter
                
                batch_headers = {
                    "Cookie": cookie_str,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    "Origin": "https://gemini.google.com",
                    "Referer": "https://gemini.google.com/app",
                    "Accept": "*/*",
                    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                    "X-Same-Domain": "1",
                    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                }
                
                resp = await client.post(
                    "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
                    data=req_data,
                    headers=batch_headers,
                    params={"bl": "boq_assistant-bard-web-server_20241001.04_p0", "_reqid": "0", "rt": "c"}
                )
                
                if resp.status_code in (401, 403):
                    ck["snlm0e"] = None
                    cookie_pool.block(cookie_id, 60)
                    raise CookieDeadError(cookie_id, f"batchexecute rejected HTTP {resp.status_code}")
                
                if resp.status_code != 200:
                    raise CookieDeadError(cookie_id, f"Gemini error HTTP {resp.status_code}: {resp.text[:80]}")

            
            # ── Step 3: Parse batchexecute response ──
            # Response format: )]}\'\r\n\r\nNUM\r\n[["wrb.fr",null,"[...inner json...]"]]\r\nNUM\r\n...
            raw = resp.text
            ai_text = ""
            
            def decode_text(t: str) -> str:
                """Decode JSON unicode escapes like \\u00d4 → Ô."""
                if '\\u' in t or '\\n' in t:
                    try:
                        return json.loads(f'"{t}"'.replace('\n', '\\n'))
                    except Exception:
                        pass
                return t
            
            try:
                # Split on actual line breaks (handles \r\n)
                for line in raw.splitlines():
                    line = line.strip()
                    if not line.startswith('[['):
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    
                    # Check it's a wrb.fr response with inner data
                    if (not isinstance(data, list) or not data 
                        or not isinstance(data[0], list) or len(data[0]) < 3 
                        or data[0][2] is None):
                        continue
                    
                    try:
                        inner = json.loads(data[0][2])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    
                    if not isinstance(inner, list) or len(inner) <= 4 or not inner[4]:
                        continue
                    
                    # inner[4] = [[resp_id, [text_parts], ...], ...]
                    candidates = inner[4]
                    for cand in candidates:
                        if isinstance(cand, list) and len(cand) > 1 and isinstance(cand[1], list):
                            # cand[1] = [text_content, ...] or [[text_piece, ...], ...]
                            text_parts = cand[1]
                            for part in text_parts:
                                if isinstance(part, str):
                                    ai_text += decode_text(part)
                                elif isinstance(part, list) and part and isinstance(part[0], str):
                                    ai_text += decode_text(part[0])
                    
                    if ai_text:
                        break  # Got text from first valid chunk
                        
            except Exception as e:
                logger.warning(f"[Cookie] Parse error: {e}")
            
            # Fallback: find the longest JSON string in the response
            if not ai_text:
                for m in sorted(re.finditer(r'"((?:[^"\\]|\\.){20,})"', raw), key=lambda x: -len(x.group(1))):
                    try:
                        candidate = json.loads(f'"{m.group(1)}"')
                        if len(candidate) > len(ai_text):
                            ai_text = candidate
                            break
                    except Exception:
                        continue
                if ai_text:
                    logger.info(f"[Cookie] Used regex fallback, got {len(ai_text)} chars")
            
            if not ai_text:
                logger.error(f"[Cookie] Empty response. Raw[:1000]={raw[:1000]}")
                raise HTTPException(status_code=500, detail="Gemini trả response rỗng — thử lại")
            
            # Return OpenAI-compatible format
            return {
                "choices": [{"message": {"content": ai_text, "role": "assistant"}}],
                "model": "gemini-cookie",
                "usage": {"total_tokens": 0}
            }
        except CookieDeadError as cde:
            last_error = str(cde)
            logger.warning(f"[CookieHotSwap] {last_error} — trying next cookie")
            continue  # Hot-swap: next iteration picks a different cookie
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Gemini timeout — thử lại")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Cookie] Translate error: {e}")
        raise HTTPException(status_code=500, detail=f"Cookie translate error: {str(e)}")
    
    # All cookies in pool failed
    raise HTTPException(
        status_code=503,
        detail=f"Tất cả {max_attempts} cookie trong pool đều thất bại. Lỗi cuối: {last_error or 'unknown'}. Hãy thêm cookie mới hoặc refresh cookie."
    )


# ── WORKER HEALTH DASHBOARD ──
@app.get("/api/worker-health")
async def worker_health():
    """Real-time health snapshot: keys, cookies, workers, throughput."""
    now = time.time()
    
    # Cookie pool health
    pool_health = []
    for ck in cookie_pool.cookies:
        blocked = ck["blocked_until"] > now
        remaining = max(0, ck["blocked_until"] - now) if blocked else 0
        pool_health.append({
            "id": ck["id"],
            "alive": not blocked,
            "blocked_for": round(remaining),
            "has_token": bool(ck.get("snlm0e")),
            "token_age": round(now - ck.get("snlm0e_time", now)) if ck.get("snlm0e") else None
        })
    
    # Key manager health (from active session if any)
    key_health = []
    from translator import _active_key_managers
    for session_id, km in list(_active_key_managers.items()):
        try:
            report = km.get_health_report()
            key_health.append({
                "session": session_id,
                "keys": report.get("key_details", []),
                "providers": report.get("providers", {})
            })
        except Exception:
            pass
    
    return {
        "timestamp": round(now),
        "active_sessions": len(active_sessions),
        "cookie_pool": {
            "total": len(pool_health),
            "alive": sum(1 for c in pool_health if c["alive"]),
            "blocked": sum(1 for c in pool_health if not c["alive"]),
            "cookies": pool_health
        },
        "key_managers": key_health
    }


@app.get("/cookie-debug")
async def cookie_debug():
    """Debug: try to get SNlM0e and run a tiny batchexecute to verify cookie health."""

    import httpx, re
    ck = cookie_pool.get_next()
    if not ck:
        return {"ok": False, "error": "No cookie"}
    
    headers = {
        "Cookie": ck["cookie"],
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
    }
    
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            page = await client.get("https://gemini.google.com/app", headers=headers)
            has_snlm0e = bool(re.search(r'"SNlM0e":"(.*?)"', page.text))
            is_login = "accounts.google" in str(page.url) or "ServiceLogin" in page.text
            
            snlm0e_val = None
            if has_snlm0e:
                m = re.search(r'"SNlM0e":"(.*?)"', page.text)
                if m:
                    snlm0e_val = m.group(1)[:20] + "..."
            
            return {
                "ok": not is_login and has_snlm0e,
                "cookie_id": ck["id"],
                "final_url": str(page.url),
                "status_code": page.status_code,
                "has_snlm0e": has_snlm0e,
                "snlm0e_preview": snlm0e_val,
                "is_redirected_login": is_login,
                "page_size": len(page.text),
                "diagnosis": "Cookie hợp lệ ✅" if (not is_login and has_snlm0e) else 
                             "Cookie hết hạn ❌ — cần lấy cookie mới" if is_login else
                             "SNlM0e không tìm thấy — trang Gemini thay đổi format"
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── SERVE FRONTEND ──
@app.get("/")
async def serve_frontend():
    return FileResponse("index.html", media_type="text/html")


# ── HEALTH CHECK ──
@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(active_sessions)}


if __name__ == "__main__":
    import uvicorn
    print("Khoi chay server API tai http://localhost:8000")
    print("  WebSocket: ws://localhost:8000/ws/translate")
    print("  SSE:       POST http://localhost:8000/translate")
    uvicorn.run(app, host="0.0.0.0", port=8000)
