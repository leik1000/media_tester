import uuid
import base64
import json
import mimetypes
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse, unquote

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import requests

import run_gemini_image_check as image_runner
import run_video_availability_check as video_runner

app = FastAPI()

# Mount static files for the web UI
app.mount("/static", StaticFiles(directory="web"), name="static")

# In-memory store for background tasks
tasks_store: Dict[str, Dict[str, Any]] = {}
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
DATA_DIR = Path(__file__).resolve().parent / "data"
CONFIG_DB_PATH = DATA_DIR / "config.db"
MANIFEST_PATH = DOWNLOAD_DIR / "manifest.json"
manifest_lock = threading.Lock()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")


def init_config_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS configs (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def read_saved_config() -> dict[str, Any]:
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        row = conn.execute("SELECT value FROM configs WHERE name = ?", ("default",)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_saved_config(data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO configs (name, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(name) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            ("default", payload),
        )


init_config_db()


def form_value(form, name: str, default: str = "") -> str:
    value = form.get(name)
    if value is None:
        return default
    return str(value)


def form_urls(form) -> list[str]:
    urls: list[str] = []
    for value in form.getlist("image_url"):
        for line in str(value or "").splitlines():
            line = line.strip()
            if line:
                urls.append(line)
    return urls


def split_reference_urls(urls: list[str]) -> tuple[list[str], list[str]]:
    remote_urls: list[str] = []
    local_files: list[str] = []
    for url in urls:
        value = str(url or "").strip()
        if not value:
            continue
        parsed = urlparse(value)
        path_value = parsed.path if parsed.scheme in {"http", "https"} else value
        if path_value.startswith("/downloads/"):
            filename = Path(unquote(path_value)).name
            local_path = DOWNLOAD_DIR / filename
            if local_path.is_file():
                local_files.append(str(local_path))
                continue
        remote_urls.append(value)
    return remote_urls, local_files


def build_proxies(proxy_url: str) -> dict[str, str] | None:
    value = str(proxy_url or "").strip()
    if not value:
        return None
    return {"http": value, "https": value}


async def save_uploaded_images(form) -> list[str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    for upload in form.getlist("image_file"):
        filename = str(getattr(upload, "filename", "") or "").strip()
        if not filename:
            continue
        suffix = Path(filename).suffix.lower() or ".png"
        target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        raw = await upload.read()
        if not raw:
            raise RuntimeError(f"上传的图片为空：{filename}")
        target.write_bytes(raw)
        saved_paths.append(str(target))
    return saved_paths


def cleanup_files(paths: list[str]) -> None:
    for item in paths:
        try:
            Path(item).unlink(missing_ok=True)
        except OSError:
            pass


def now_label() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def choose_image_suffix(mime_type: str) -> str:
    suffix = mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip())
    if suffix == ".jpe":
        return ".jpg"
    return suffix or ".png"


def public_download_url(path: Path) -> str:
    return f"/downloads/{path.name}"


def read_manifest() -> list[dict[str, Any]]:
    if not MANIFEST_PATH.is_file():
        return []
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def write_manifest(items: list[dict[str, Any]]) -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def register_asset(asset: dict[str, Any]) -> dict[str, Any]:
    with manifest_lock:
        items = read_manifest()
        items.insert(0, asset)
        write_manifest(items)
    return asset


def save_image_asset(image_bytes: bytes, mime_type: str, settings: dict[str, Any]) -> dict[str, Any]:
    asset_id = uuid.uuid4().hex
    suffix = choose_image_suffix(mime_type)
    file_path = DOWNLOAD_DIR / f"image_{asset_id}{suffix}"
    file_path.write_bytes(image_bytes)
    model = str(settings.get("model") or "")
    meta = f"{settings.get('image_size')} · {settings.get('aspect_ratio')}"
    if image_runner.is_openai_image_model(model):
        meta = f"{meta} · {settings.get('quality') or image_runner.DEFAULT_OPENAI_QUALITY}"
    return register_asset({
        "id": asset_id,
        "type": "image",
        "status": "completed",
        "url": public_download_url(file_path),
        "filename": file_path.name,
        "model": model,
        "prompt": str(settings.get("prompt") or ""),
        "meta": meta,
        "createdAt": now_label(),
    })


def save_video_asset(media_url: str, settings: dict[str, Any]) -> dict[str, Any]:
    asset_id = uuid.uuid4().hex
    response = requests.get(
        media_url,
        allow_redirects=True,
        timeout=int(settings["request_timeout"]),
        proxies=settings.get("proxies"),
    )
    response.raise_for_status()
    suffix = video_runner.choose_suffix(str(response.headers.get("content-type") or ""), media_url)
    file_path = DOWNLOAD_DIR / f"video_{asset_id}{suffix}"
    file_path.write_bytes(response.content)
    return register_asset({
        "id": asset_id,
        "type": "video",
        "status": "completed",
        "url": public_download_url(file_path),
        "remote_url": media_url,
        "filename": file_path.name,
        "model": str(settings.get("model") or ""),
        "prompt": str(settings.get("prompt") or ""),
        "meta": f"{settings.get('duration')}s · {settings.get('aspect_ratio')}",
        "createdAt": now_label(),
    })

@app.get("/")
def read_root():
    return FileResponse("web/index.html")


@app.get("/api/config")
def get_config():
    return {"config": read_saved_config()}


@app.post("/api/config")
async def save_config(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"message": "配置必须是 JSON 对象"})
    write_saved_config(data)
    return {"status": "ok"}


@app.get("/api/assets")
def list_assets():
    with manifest_lock:
        assets = read_manifest()
    existing = []
    for item in assets:
        filename = str(item.get("filename") or "")
        if filename and (DOWNLOAD_DIR / filename).is_file():
            existing.append(item)
    return {"assets": existing}


def run_image_task(internal_task_id: str, settings: dict):
    tasks_store[internal_task_id]["status"] = "running"
    tasks_store[internal_task_id]["logs"].append("图片请求已开始。")

    try:
        payload = image_runner.build_payload(settings)
        tasks_store[internal_task_id]["logs"].append("图片请求参数已构建。")
        data = image_runner.create_image(settings, payload)
        tasks_store[internal_task_id]["logs"].append("已收到图片响应。")
        image_bytes, mime_type = image_runner.extract_inline_image(data, proxies=settings.get("proxies"))
        asset = save_image_asset(image_bytes, mime_type, settings)
        tasks_store[internal_task_id]["logs"].append(f"已保存到 downloads/{asset['filename']}。")

        tasks_store[internal_task_id]["status"] = "completed"
        tasks_store[internal_task_id]["image_url"] = asset["url"]
        tasks_store[internal_task_id]["asset"] = asset
        tasks_store[internal_task_id]["raw"] = data
    except Exception as e:
        tasks_store[internal_task_id]["status"] = "error"
        tasks_store[internal_task_id]["error"] = str(e)
        tasks_store[internal_task_id]["logs"].append(f"错误：{str(e)}")
    finally:
        cleanup_files(list(settings.get("_temp_files") or []))


@app.post("/api/image")
async def generate_image(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    temp_files = await save_uploaded_images(form)
    remote_urls, local_reference_files = split_reference_urls(form_urls(form))
    settings = {
        "base_url": form_value(form, "base_url", image_runner.DEFAULT_BASE_URL),
        "proxy_url": form_value(form, "proxy_url"),
        "api_key": form_value(form, "api_key"),
        "model": form_value(form, "model", image_runner.DEFAULT_MODEL),
        "prompt": form_value(form, "prompt", image_runner.DEFAULT_PROMPT),
        "aspect_ratio": form_value(form, "aspect_ratio", image_runner.DEFAULT_ASPECT_RATIO),
        "image_size": form_value(form, "image_size", image_runner.DEFAULT_IMAGE_SIZE),
        "quality": form_value(form, "quality", image_runner.DEFAULT_OPENAI_QUALITY),
        "image_url": remote_urls,
        "image_file": temp_files + local_reference_files,
        "_temp_files": temp_files,
    }
    settings["request_timeout"] = 600
    settings["proxies"] = build_proxies(settings.get("proxy_url", ""))

    internal_task_id = str(uuid.uuid4())
    tasks_store[internal_task_id] = {
        "type": "image",
        "status": "pending",
        "logs": [],
        "image_url": None,
        "raw": None,
        "error": None,
    }

    background_tasks.add_task(run_image_task, internal_task_id, settings)
    return {"internal_task_id": internal_task_id}

def run_video_task(internal_task_id: str, settings: dict):
    tasks_store[internal_task_id]["status"] = "running"
    tasks_store[internal_task_id]["logs"].append("视频请求已开始。")
    
    try:
        payload = video_runner.build_payload(settings)
        tasks_store[internal_task_id]["logs"].append("视频请求参数已构建。")
        create_data = video_runner.create_task(settings, payload)
        api_task_id = video_runner.extract_task_id(create_data)
        tasks_store[internal_task_id]["api_task_id"] = api_task_id
        tasks_store[internal_task_id]["logs"].append(f"远程任务 ID：{api_task_id}")
        
        result = video_runner.poll_task(settings, api_task_id)
        status = str(result.get("status") or "").strip().lower()
        
        if status != "completed":
            tasks_store[internal_task_id]["status"] = "failed"
            tasks_store[internal_task_id]["raw"] = result
            return
            
        media_url = video_runner.resolve_media_url(result)
        tasks_store[internal_task_id]["logs"].append("正在下载视频到本地。")
        asset = save_video_asset(media_url, settings)
        tasks_store[internal_task_id]["logs"].append(f"已保存到 downloads/{asset['filename']}。")
        tasks_store[internal_task_id]["status"] = "completed"
        tasks_store[internal_task_id]["media_url"] = asset["url"]
        tasks_store[internal_task_id]["remote_url"] = media_url
        tasks_store[internal_task_id]["asset"] = asset
        tasks_store[internal_task_id]["raw"] = result
        
    except Exception as e:
        tasks_store[internal_task_id]["status"] = "error"
        tasks_store[internal_task_id]["error"] = str(e)
        tasks_store[internal_task_id]["logs"].append(f"错误：{str(e)}")
    finally:
        cleanup_files(list(settings.get("_temp_files") or []))

@app.post("/api/video")
async def generate_video(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()
    temp_files = await save_uploaded_images(form)
    remote_urls, local_reference_files = split_reference_urls(form_urls(form))
    settings = {
        "base_url": form_value(form, "base_url", video_runner.DEFAULT_BASE_URL),
        "proxy_url": form_value(form, "proxy_url"),
        "api_key": form_value(form, "api_key"),
        "create_path": form_value(form, "create_path", video_runner.DEFAULT_CREATE_PATH),
        "status_path": form_value(form, "status_path", video_runner.DEFAULT_STATUS_PATH),
        "model": form_value(form, "model", video_runner.DEFAULT_MODEL),
        "prompt": form_value(form, "prompt", video_runner.DEFAULT_PROMPT),
        "aspect_ratio": form_value(form, "aspect_ratio", video_runner.DEFAULT_ASPECT_RATIO),
        "size": form_value(form, "size"),
        "duration": int(form_value(form, "duration", str(video_runner.DEFAULT_DURATION)) or video_runner.DEFAULT_DURATION),
        "resolution": form_value(form, "resolution"),
        "start_frame": form_value(form, "start_frame"),
        "end_frame": form_value(form, "end_frame"),
        "video_reference": form_value(form, "video_reference"),
        "image_url": remote_urls,
        "image_file": temp_files + local_reference_files,
        "_temp_files": temp_files,
    }
    settings["request_timeout"] = 600
    settings["proxies"] = build_proxies(settings.get("proxy_url", ""))
    settings["poll_interval"] = 10.0
    settings["timeout"] = 600
    settings["reference_format"] = "array"
    settings["reference_field"] = "input_reference"
    settings["extra_field"] = []

    internal_task_id = str(uuid.uuid4())
    tasks_store[internal_task_id] = {
        "type": "video",
        "status": "pending",
        "logs": [],
        "media_url": None,
        "raw": None,
        "error": None,
        "api_task_id": None
    }
    
    background_tasks.add_task(run_video_task, internal_task_id, settings)
    return {"internal_task_id": internal_task_id}

@app.get("/api/task/{internal_task_id}")
async def get_task_status(internal_task_id: str):
    if internal_task_id not in tasks_store:
        return JSONResponse(status_code=404, content={"message": "Task not found"})
    return tasks_store[internal_task_id]
