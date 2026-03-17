"""
🍪 Lấy Cookie Gemini — Dùng Chrome Remote Debugging
Cách dùng: python get_cookie.py

Yêu cầu: Chrome phải đang chạy với --remote-debugging-port
Nếu chưa, script sẽ tự mở Chrome với flag này.
"""
import os, sys, json, subprocess, time, urllib.request

CHROME_DEBUG_PORT = 9222

def find_chrome():
    """Tìm Chrome executable."""
    paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def is_debug_port_open():
    """Check if Chrome debug port is available."""
    try:
        req = urllib.request.urlopen(f"http://localhost:{CHROME_DEBUG_PORT}/json/version", timeout=2)
        data = json.loads(req.read())
        return True, data.get("Browser", "Chrome")
    except:
        return False, None


def launch_chrome_debug():
    """Launch Chrome with remote debugging."""
    chrome = find_chrome()
    if not chrome:
        return False, "Chrome not found"
    
    print(f"🚀 Mở Chrome với remote debugging port {CHROME_DEBUG_PORT}...")
    subprocess.Popen([
        chrome,
        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
        "--restore-last-session",  # Giữ tab cũ
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for Chrome to start
    for i in range(10):
        time.sleep(1)
        ok, _ = is_debug_port_open()
        if ok:
            return True, "Chrome started"
    return False, "Chrome started but debug port not responding"


def get_cookies_via_cdp():
    """Get Google cookies via Chrome DevTools Protocol."""
    try:
        # Get available pages
        req = urllib.request.urlopen(f"http://localhost:{CHROME_DEBUG_PORT}/json", timeout=5)
        pages = json.loads(req.read())
        
        if not pages:
            return None, "No Chrome pages found"
        
        # Use websocket to send CDP command
        # Simple HTTP approach: use /json/protocol for cookie access
        # Actually, we need to use the browser-level CDP endpoint
        
        # Get browser websocket URL
        req = urllib.request.urlopen(f"http://localhost:{CHROME_DEBUG_PORT}/json/version", timeout=5)
        version = json.loads(req.read())
        ws_url = version.get("webSocketDebuggerUrl", "")
        
        if not ws_url:
            return None, "Cannot get WebSocket URL"
        
        # Use websocket to get cookies
        import websocket
        ws = websocket.create_connection(ws_url, timeout=5)
        
        # Send Network.getAllCookies command
        ws.send(json.dumps({
            "id": 1,
            "method": "Storage.getCookies",
            "params": {}
        }))
        
        result = json.loads(ws.recv())
        ws.close()
        
        if "result" not in result:
            # Try Network.getAllCookies instead
            ws = websocket.create_connection(ws_url, timeout=5)
            ws.send(json.dumps({
                "id": 1,
                "method": "Network.getAllCookies",
                "params": {}
            }))
            result = json.loads(ws.recv())
            ws.close()
        
        all_cookies = result.get("result", {}).get("cookies", [])
        
        # Filter Google cookies
        google_cookies = {}
        for c in all_cookies:
            domain = c.get("domain", "")
            if "google" in domain or "youtube" in domain:
                google_cookies[c["name"]] = c["value"]
        
        if google_cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in google_cookies.items())
            return cookie_str, google_cookies
        
        return None, f"Found {len(all_cookies)} total cookies but none for Google"
        
    except ImportError:
        return None, "NEED_WEBSOCKET"
    except Exception as e:
        return None, str(e)


def get_cookies_simple():
    """Fallback: get cookies via simple HTTP endpoint - chỉ lấy từ page đang mở."""
    try:
        # Get list of pages
        req = urllib.request.urlopen(f"http://localhost:{CHROME_DEBUG_PORT}/json", timeout=5)
        pages = json.loads(req.read())
        
        # Find any Google page
        google_page = None
        for p in pages:
            url = p.get("url", "")
            if "google.com" in url or "gemini.google" in url:
                google_page = p
                break
        
        if not google_page:
            # If no Google page open, navigate to one
            print("📝 Không thấy trang Google đang mở, thử mở gemini.google.com...")
            if pages:
                ws_url = pages[0].get("webSocketDebuggerUrl", "")
                if ws_url:
                    import websocket
                    ws = websocket.create_connection(ws_url, timeout=10)
                    ws.send(json.dumps({
                        "id": 1,
                        "method": "Page.navigate",
                        "params": {"url": "https://gemini.google.com"}
                    }))
                    ws.recv()
                    time.sleep(3)
                    
                    # Now get cookies
                    ws.send(json.dumps({
                        "id": 2,
                        "method": "Network.getCookies",
                        "params": {"urls": ["https://gemini.google.com", "https://accounts.google.com", "https://www.google.com"]}
                    }))
                    result = json.loads(ws.recv())
                    ws.close()
                    
                    cookies = result.get("result", {}).get("cookies", [])
                    if cookies:
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
                        return cookie_str, cookie_dict
        
        return None, "Could not get cookies"
    except ImportError:
        return None, "NEED_WEBSOCKET"
    except Exception as e:
        return None, str(e)


if __name__ == "__main__":
    print("🍪 Lấy Cookie Gemini tự động\n")
    
    # 1. Check if Chrome debug port is open
    ok, browser = is_debug_port_open()
    if not ok:
        print("⚠️ Chrome chưa mở debug port. Đang mở Chrome...")
        ok, msg = launch_chrome_debug()
        if not ok:
            print(f"❌ {msg}")
            print("\n💡 Mở Chrome thủ công với lệnh:")
            print(f'   chrome.exe --remote-debugging-port={CHROME_DEBUG_PORT}')
            sys.exit(1)
    
    print(f"✅ Chrome debug port {CHROME_DEBUG_PORT} sẵn sàng")
    
    # 2. Install websocket-client if needed
    try:
        import websocket
    except ImportError:
        print("📦 Cài websocket-client...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websocket-client", "-q"])
        import websocket
    
    # 3. Get cookies via CDP
    print("🔍 Đang lấy cookie...")
    cookie_str, info = get_cookies_via_cdp()
    
    if not cookie_str:
        print(f"⚠️ CDP method: {info}")
        print("🔄 Thử phương pháp backup...")
        cookie_str, info = get_cookies_simple()
    
    if cookie_str:
        cookie_dict = info if isinstance(info, dict) else {}
        print(f"\n✅ Lấy cookie thành công!")
        print(f"📊 Số cookie: {len(cookie_str.split(';'))}")
        
        # Show important cookie names
        key_cookies = ["__Secure-1PSID", "__Secure-1PSIDTS", "__Secure-1PSIDCC", "SID", "NID", "APISID", "SAPISID"]
        found = [k for k in key_cookies if k in cookie_str]
        missing = [k for k in ["__Secure-1PSID", "SID"] if k not in cookie_str]
        print(f"🔑 Cookie quan trọng: {', '.join(found)}")
        if missing:
            print(f"⚠️ Thiếu: {', '.join(missing)} — hãy đăng nhập gemini.google.com trước!")
        
        # Save to file
        out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_cookie.txt")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        print(f"\n💾 Đã lưu: {out_file}")
        
        # Try to send to server
        try:
            data = json.dumps({"cookie": cookie_str}).encode()
            req = urllib.request.Request(
                "http://localhost:8000/cookie-set",
                data=data,
                headers={"Content-Type": "application/json"}
            )
            resp = urllib.request.urlopen(req, timeout=3)
            print("🚀 Đã gửi cookie lên server thành công!")
        except:
            print("💡 Server chưa chạy — paste nội dung file vào ô Cookie trong app")
        
        print(f"\n--- TÊN COOKIE ---")
        for name in sorted(cookie_dict.keys() if cookie_dict else cookie_str.split("; ")):
            if isinstance(name, str) and "=" in name:
                name = name.split("=")[0]
            print(f"  • {name}")
    else:
        print(f"\n❌ Không lấy được cookie: {info}")
        print("\n💡 Giải pháp:")
        print("  1. Đăng nhập gemini.google.com trên Chrome")
        print(f"  2. Đóng Chrome → mở lại với: chrome.exe --remote-debugging-port={CHROME_DEBUG_PORT}")
        print("  3. Chạy lại: python get_cookie.py")
