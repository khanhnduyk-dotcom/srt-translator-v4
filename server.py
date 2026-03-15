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
from contextlib import asynccontextmanager

# Set UTF-8 encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
    
# Ensure current directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from translator import run_translation


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


@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(cleanup_temp_files())
    wf_task = asyncio.create_task(watchfolder_scanner())
    yield
    task.cancel()
    wf_task.cancel()

app = FastAPI(lifespan=lifespan)

# Cấp quyền CORS để gọi từ frontend HTML local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Serve frontend ──
@app.get("/")
async def serve_frontend():
    return FileResponse("index.html", media_type="text/html")
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
