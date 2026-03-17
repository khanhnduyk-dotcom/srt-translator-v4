"""Debug: test cookie-translate and show raw response"""
import httpx, json, re, time, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie_store.json")
with open(COOKIE_FILE, 'r') as f:
    cookie_str = json.load(f).get("cookie", "")

print(f"Cookie: {len(cookie_str)} chars")

headers = {
    "Cookie": cookie_str,
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin": "https://gemini.google.com",
    "Referer": "https://gemini.google.com/",
}

# Step 1: Get SNlM0e
with httpx.Client(timeout=30, follow_redirects=True) as client:
    page = client.get("https://gemini.google.com/app", headers=headers)
    print(f"Page status: {page.status_code}, url: {page.url}")
    
    m = re.search(r'"SNlM0e":"(.*?)"', page.text)
    if not m:
        print("❌ No SNlM0e found!")
        # Check first 500 chars
        print(f"Page start: {page.text[:500]}")
        sys.exit(1)
    
    snlm0e = m.group(1)
    print(f"SNlM0e: {snlm0e[:20]}...")
    
    # Step 2: Send prompt
    prompt_text = "Dịch câu này sang tiếng Việt: Hello, how are you?"
    
    inner = json.dumps([prompt_text, 0, None])
    outer = json.dumps([[inner, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None, None]])
    
    req_data = {
        "f.req": json.dumps([None, outer]),
        "at": snlm0e,
    }
    
    batch_headers = {**headers, "Content-Type": "application/x-www-form-urlencoded"}
    
    resp = client.post(
        "https://gemini.google.com/_/BardChatUi/data/assistant.lamda.BardFrontendService/StreamGenerate",
        data=req_data,
        headers=batch_headers,
        params={"bl": "boq_assistant-bard-web-server_20241001.04_p0", "_reqid": "0", "rt": "c"}
    )
    
    print(f"\nResponse status: {resp.status_code}")
    print(f"Response length: {len(resp.text)} chars")
    
    raw = resp.text
    
    # Save full response for analysis
    with open("debug_response.txt", "w", encoding="utf-8") as f:
        f.write(raw)
    print(f"\n💾 Full response saved to debug_response.txt")
    
    # Show structure
    print("\n=== RAW RESPONSE (first 2000 chars) ===")
    print(raw[:2000])
    
    # Try to parse
    print("\n=== PARSING ATTEMPTS ===")
    lines = raw.split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        if line.startswith('['):
            try:
                parsed = json.loads(line)
                print(f"\nLine {i}: JSON array, len={len(parsed)}")
                if isinstance(parsed, list) and len(parsed) > 0:
                    if isinstance(parsed[0], list) and len(parsed[0]) > 0:
                        print(f"  [0] = list, len={len(parsed[0])}")
                        if len(parsed[0]) > 2 and parsed[0][2]:
                            inner_str = parsed[0][2]
                            print(f"  [0][2] type={type(inner_str).__name__}, len={len(str(inner_str))}")
                            if isinstance(inner_str, str):
                                try:
                                    inner_parsed = json.loads(inner_str)
                                    print(f"  inner_parsed type={type(inner_parsed).__name__}")
                                    if isinstance(inner_parsed, list):
                                        print(f"  inner_parsed len={len(inner_parsed)}")
                                        for j, item in enumerate(inner_parsed):
                                            if item is not None:
                                                desc = str(item)[:100]
                                                print(f"    [{j}] = {type(item).__name__}: {desc}")
                                except:
                                    print(f"  [0][2] = {inner_str[:200]}")
            except json.JSONDecodeError:
                if len(line) > 5:
                    print(f"\nLine {i}: not JSON, first 50: {line[:50]}")
