"""
SRT Translator Launcher — Khởi động Backend + Frontend song song.
Double-click file này hoặc chạy: python launcher.py
- Tự cài dependencies nếu thiếu
- Tự restart backend nếu crash
- Mở trình duyệt tự động
"""
import os
import sys
import time
import socket
import threading
import webbrowser
import subprocess
import http.server
import socketserver

# Đường dẫn gốc
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(BASE_DIR)

# Tạo thư mục cần thiết
for d in ['temp_uploads', 'srt_in', 'srt_out']:
    os.makedirs(d, exist_ok=True)


def check_and_install_deps():
    """Tự cài dependencies nếu thiếu."""
    required = ['fastapi', 'uvicorn', 'httpx']
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    
    if missing:
        print(f"📦 Cài đặt dependencies: {', '.join(missing)}...")
        req_file = os.path.join(BASE_DIR, 'requirements.txt')
        if os.path.exists(req_file):
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', req_file, '-q'])
        else:
            subprocess.check_call([sys.executable, '-m', 'pip', 'install'] + missing + ['-q'])
        print("✅ Cài đặt xong!\n")


def is_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0


def kill_port(port):
    """Kill process đang chiếm port (Windows)."""
    try:
        result = subprocess.run(
            f'netstat -ano | findstr :{port}',
            capture_output=True, text=True, shell=True
        )
        for line in result.stdout.strip().split('\n'):
            if f':{port}' in line and 'LISTENING' in line:
                pid = line.strip().split()[-1]
                subprocess.run(f'taskkill /F /PID {pid}', shell=True,
                             capture_output=True)
                print(f"   🔄 Đã tắt process cũ trên port {port} (PID {pid})")
    except Exception:
        pass


def start_frontend(port=8080):
    """Serve index.html trên HTTP server."""
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a: None  # Tắt log
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            httpd.serve_forever()
    except Exception as e:
        print(f"❌ Frontend error: {e}")


def start_backend_with_restart(max_restarts=5):
    """Start FastAPI backend với auto-restart khi crash."""
    import uvicorn
    sys.path.insert(0, BASE_DIR)
    
    restarts = 0
    while restarts < max_restarts:
        try:
            print(f"   🔧 Backend {'restarting...' if restarts > 0 else 'starting...'}")
            uvicorn.run("server:app", host="0.0.0.0", port=8000,
                       log_level="warning", access_log=False)
            break  # Clean exit
        except Exception as e:
            restarts += 1
            print(f"   ⚠️ Backend crashed ({restarts}/{max_restarts}): {e}")
            if restarts < max_restarts:
                time.sleep(2)
            else:
                print(f"   ❌ Backend failed after {max_restarts} restarts!")


def wait_for_server(port, timeout=10):
    """Đợi server sẵn sàng."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(('localhost', port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   🚀 SRT TRANSLATOR — ĐANG KHỞI ĐỘNG    ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # Step 1: Check & install dependencies
    check_and_install_deps()

    # Step 2: Kill old processes nếu port bận
    for port in [8000, 8080]:
        if not is_port_free(port):
            print(f"   ⚠️ Port {port} đang bận — đang tắt process cũ...")
            kill_port(port)
            time.sleep(1)

    # Step 3: Start backend (daemon thread, auto-restart)
    print("🔧 Khởi động Backend server (port 8000)...")
    backend_thread = threading.Thread(target=start_backend_with_restart, daemon=True)
    backend_thread.start()

    # Step 4: Start frontend
    print("🌐 Khởi động Frontend server (port 8080)...")
    frontend_thread = threading.Thread(target=start_frontend, daemon=True)
    frontend_thread.start()

    # Step 5: Đợi backend sẵn sàng
    if wait_for_server(8000):
        print("   ✅ Backend sẵn sàng!")
    else:
        print("   ⚠️ Backend chậm khởi động, tiếp tục...")

    if wait_for_server(8080):
        print("   ✅ Frontend sẵn sàng!")

    print()
    print("═══════════════════════════════════════════")
    print("  ✅ ĐÃ KHỞI ĐỘNG THÀNH CÔNG!")
    print()
    print("  🌐 Frontend: http://localhost:8080")
    print("  🔧 Backend:  http://localhost:8000")
    print("═══════════════════════════════════════════")
    print()

    # Mở trình duyệt
    webbrowser.open("http://localhost:8080")

    print("🟢 Đang chạy. Nhấn Ctrl+C hoặc đóng cửa sổ để tắt tất cả.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        print("🔴 Đã tắt server.")


if __name__ == "__main__":
    main()
