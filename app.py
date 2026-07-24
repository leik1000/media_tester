import uuid
import hashlib
import hmac
import json
import mimetypes
import secrets
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from urllib.parse import urlparse, unquote

from fastapi import FastAPI, Request, BackgroundTasks, Query
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import requests

from tests import run_gemini_image_check as image_runner
from tests import run_video_availability_check as video_runner

app = FastAPI()

# Mount static files for the web UI
app.mount("/static", StaticFiles(directory="web"), name="static")

# In-memory store for background tasks
tasks_store: Dict[str, Dict[str, Any]] = {}
UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
DOWNLOAD_DIR = Path(__file__).resolve().parent / "downloads"
THUMB_DIR = DOWNLOAD_DIR / "thumbs"
DATA_DIR = Path(__file__).resolve().parent / "data"
CONFIG_DB_PATH = DATA_DIR / "config.db"
SESSION_COOKIE = "media_tester_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
THUMBNAIL_SIZE = (480, 270)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS media_tasks (
                id TEXT PRIMARY KEY,
                remote_task_id TEXT,
                type TEXT NOT NULL,
                status TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt TEXT NOT NULL,
                meta TEXT,
                request_payload TEXT,
                logs TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                raw TEXT,
                local_url TEXT,
                remote_url TEXT,
                filename TEXT,
                thumbnail_url TEXT,
                thumbnail_filename TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_seconds INTEGER
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(media_tasks)").fetchall()}
        if "thumbnail_url" not in columns:
            conn.execute("ALTER TABLE media_tasks ADD COLUMN thumbnail_url TEXT")
        if "thumbnail_filename" not in columns:
            conn.execute("ALTER TABLE media_tasks ADD COLUMN thumbnail_filename TEXT")


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


def read_named_config(name: str) -> dict[str, Any]:
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        row = conn.execute("SELECT value FROM configs WHERE name = ?", (name,)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_named_config(name: str, data: dict[str, Any]) -> None:
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
            (name, payload),
        )


def hash_password(password: str, salt: str) -> str:
    raw = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return raw.hex()


def ensure_auth_config() -> dict[str, Any]:
    auth = read_named_config("auth")
    if auth.get("username") and auth.get("password_hash") and auth.get("salt"):
        return auth
    salt = secrets.token_hex(16)
    auth = {
        "username": "admin",
        "salt": salt,
        "password_hash": hash_password("admin", salt),
    }
    write_named_config("auth", auth)
    return auth


def verify_credentials(username: str, password: str) -> bool:
    auth = ensure_auth_config()
    expected_username = str(auth.get("username") or "")
    salt = str(auth.get("salt") or "")
    expected_hash = str(auth.get("password_hash") or "")
    if not expected_username or not salt or not expected_hash:
        return False
    candidate_hash = hash_password(password, salt)
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(candidate_hash, expected_hash)


def session_secret() -> str:
    auth = ensure_auth_config()
    secret = str(auth.get("session_secret") or "")
    if secret:
        return secret
    auth["session_secret"] = secrets.token_hex(32)
    write_named_config("auth", auth)
    return str(auth["session_secret"])


def create_session_token(username: str) -> str:
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{username}:{expires_at}"
    signature = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def valid_session_token(token: str) -> bool:
    try:
        username, expires_at_raw, signature = str(token or "").rsplit(":", 2)
        expires_at = int(expires_at_raw)
    except (ValueError, TypeError):
        return False
    if expires_at < int(time.time()):
        return False
    payload = f"{username}:{expires_at}"
    expected = hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    return username == str(ensure_auth_config().get("username") or "")


def is_authenticated(request: Request) -> bool:
    return valid_session_token(request.cookies.get(SESSION_COOKIE, ""))


init_config_db()
ensure_auth_config()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    public_paths = {"/", "/login", "/api/auth/status", "/api/auth/login"}
    if path in public_paths or path.startswith("/static/"):
        return await call_next(request)
    if not is_authenticated(request):
        return JSONResponse(status_code=401, content={"message": "请先登录"})
    return await call_next(request)


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


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def task_meta(task_type: str, settings: dict[str, Any]) -> str:
    if task_type == "image":
        meta = f"{settings.get('image_size')} · {settings.get('aspect_ratio')}"
        if image_runner.is_openai_image_model(str(settings.get("model") or "")):
            meta = f"{meta} · {settings.get('quality') or image_runner.DEFAULT_OPENAI_QUALITY}"
        return meta
    return f"{settings.get('duration')}s · {settings.get('aspect_ratio')} · {settings.get('resolution')}"


def db_create_task(task_id: str, task_type: str, settings: dict[str, Any]) -> None:
    now = now_label()
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO media_tasks (
                id, type, status, model, prompt, meta, logs, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                task_type,
                "pending",
                str(settings.get("model") or ""),
                str(settings.get("prompt") or ""),
                task_meta(task_type, settings),
                "[]",
                now,
            ),
        )


def db_update_task(task_id: str, **fields: Any) -> None:
    allowed = {
        "remote_task_id", "status", "request_payload", "logs", "error", "raw",
        "local_url", "remote_url", "filename", "thumbnail_url",
        "thumbnail_filename", "started_at", "finished_at", "duration_seconds",
    }
    updates = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"request_payload", "logs", "raw"}:
            value = json_dumps(value)
        updates.append(f"{key} = ?")
        values.append(value)
    if not updates:
        return
    values.append(task_id)
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.execute(f"UPDATE media_tasks SET {', '.join(updates)} WHERE id = ?", values)


def db_mark_started(task_id: str, store: dict[str, Any]) -> None:
    started_at = now_label()
    store["started_at"] = started_at
    store["started_ts"] = time.time()
    db_update_task(task_id, status="running", started_at=started_at, logs=store.get("logs") or [])


def db_mark_finished(task_id: str, store: dict[str, Any], status: str) -> None:
    finished_at = now_label()
    duration = None
    started_ts = store.get("started_ts")
    if started_ts:
        duration = max(0, int(round(time.time() - float(started_ts))))
    store["finished_at"] = finished_at
    store["duration_seconds"] = duration
    db_update_task(
        task_id,
        status=status,
        finished_at=finished_at,
        duration_seconds=duration,
        logs=store.get("logs") or [],
        error=store.get("error"),
        raw=store.get("raw"),
    )


def db_task_to_client(row: sqlite3.Row, include_detail: bool = True) -> dict[str, Any]:
    status = str(row["status"] or "")
    local_url = row["local_url"]
    thumbnail_url = row["thumbnail_url"]
    task = {
        "id": row["id"],
        "taskId": row["id"],
        "internal_task_id": row["id"],
        "api_task_id": row["remote_task_id"],
        "apiTaskId": row["remote_task_id"],
        "type": row["type"],
        "status": status,
        "model": row["model"],
        "prompt": row["prompt"],
        "meta": row["meta"],
        "url": local_url,
        "local_url": local_url,
        "thumbnail_url": thumbnail_url,
        "thumbnailUrl": thumbnail_url,
        "image_url": local_url if row["type"] == "image" else None,
        "media_url": local_url if row["type"] == "video" else None,
        "asset": {
            "url": local_url,
            "filename": row["filename"],
            "remote_url": row["remote_url"],
            "thumbnail_url": thumbnail_url,
            "thumbnailUrl": thumbnail_url,
            "thumbnail_filename": row["thumbnail_filename"],
            "createdAt": row["created_at"],
        } if local_url else None,
        "remote_url": row["remote_url"],
        "filename": row["filename"],
        "thumbnail_filename": row["thumbnail_filename"],
        "logs": json_loads(row["logs"], []) if include_detail else [],
        "error": row["error"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "durationSeconds": row["duration_seconds"],
    }
    if include_detail:
        request_payload = json_loads(row["request_payload"], None)
        task["raw"] = json_loads(row["raw"], None)
        task["request_payload"] = request_payload
        task["requestPayload"] = request_payload
    return task


def db_get_task(task_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM media_tasks WHERE id = ?", (task_id,)).fetchone()
    return db_task_to_client(row) if row else None


def db_list_tasks(page: int = 1, page_size: int = 25, task_type: str = "all") -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 25)))
    offset = (page - 1) * page_size
    task_type = str(task_type or "all").strip().lower()
    where = ""
    params: list[Any] = []
    if task_type in {"image", "video"}:
        where = "WHERE type = ?"
        params.append(task_type)
    with sqlite3.connect(CONFIG_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = int(conn.execute(f"SELECT COUNT(*) FROM media_tasks {where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM media_tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "tasks": [db_task_to_client(row, include_detail=False) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "type": task_type if task_type in {"image", "video"} else "all",
    }


def choose_image_suffix(mime_type: str) -> str:
    suffix = mimetypes.guess_extension(str(mime_type or "").split(";", 1)[0].strip())
    if suffix == ".jpe":
        return ".jpg"
    return suffix or ".png"


def public_download_url(path: Path) -> str:
    try:
        relative = path.relative_to(DOWNLOAD_DIR).as_posix()
    except ValueError:
        relative = path.name
    return f"/downloads/{relative}"


def save_image_thumbnail(source_path: Path, asset_id: str) -> dict[str, str] | None:
    try:
        from PIL import Image, ImageOps

        THUMB_DIR.mkdir(parents=True, exist_ok=True)
        thumb_path = THUMB_DIR / f"thumb_{asset_id}.jpg"
        with Image.open(source_path) as image:
            fitted = ImageOps.fit(image.convert("RGB"), THUMBNAIL_SIZE, method=Image.Resampling.LANCZOS)
            fitted.save(thumb_path, "JPEG", quality=82, optimize=True)
        return {"thumbnail_url": public_download_url(thumb_path), "thumbnail_filename": thumb_path.name}
    except Exception:
        return None


def save_video_thumbnail(source_path: Path, asset_id: str) -> dict[str, str] | None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    thumb_path = THUMB_DIR / f"thumb_{asset_id}.jpg"
    filters = "scale=480:270:force_original_aspect_ratio=increase,crop=480:270"
    for seek in ("1", "0"):
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", seek, "-i", str(source_path),
                    "-vf", filters, "-frames:v", "1", "-q:v", "4", str(thumb_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0 and thumb_path.is_file() and thumb_path.stat().st_size > 0:
            return {"thumbnail_url": public_download_url(thumb_path), "thumbnail_filename": thumb_path.name}
    return None


def save_image_asset(image_bytes: bytes, mime_type: str, settings: dict[str, Any]) -> dict[str, Any]:
    asset_id = uuid.uuid4().hex
    suffix = choose_image_suffix(mime_type)
    file_path = DOWNLOAD_DIR / f"image_{asset_id}{suffix}"
    file_path.write_bytes(image_bytes)
    thumbnail = save_image_thumbnail(file_path, asset_id) or {}
    model = str(settings.get("model") or "")
    meta = f"{settings.get('image_size')} · {settings.get('aspect_ratio')}"
    if image_runner.is_openai_image_model(model):
        meta = f"{meta} · {settings.get('quality') or image_runner.DEFAULT_OPENAI_QUALITY}"
    return {
        "id": asset_id,
        "type": "image",
        "status": "completed",
        "url": public_download_url(file_path),
        "filename": file_path.name,
        "thumbnail_url": thumbnail.get("thumbnail_url"),
        "thumbnail_filename": thumbnail.get("thumbnail_filename"),
        "model": model,
        "prompt": str(settings.get("prompt") or ""),
        "meta": meta,
        "createdAt": now_label(),
    }


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
    thumbnail = save_video_thumbnail(file_path, asset_id) or {}
    return {
        "id": asset_id,
        "type": "video",
        "status": "completed",
        "url": public_download_url(file_path),
        "remote_url": media_url,
        "filename": file_path.name,
        "thumbnail_url": thumbnail.get("thumbnail_url"),
        "thumbnail_filename": thumbnail.get("thumbnail_filename"),
        "model": str(settings.get("model") or ""),
        "prompt": str(settings.get("prompt") or ""),
        "meta": f"{settings.get('duration')}s · {settings.get('aspect_ratio')}",
        "createdAt": now_label(),
    }

@app.get("/")
def read_root(request: Request):
    if not is_authenticated(request):
        return RedirectResponse(url="/login")
    return FileResponse("web/index.html")


@app.get("/login")
def read_login(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/")
    return FileResponse("web/login.html")


@app.get("/api/auth/status")
def auth_status(request: Request):
    auth = ensure_auth_config()
    return {
        "authenticated": is_authenticated(request),
        "username": str(auth.get("username") or "admin"),
    }


@app.post("/api/auth/login")
async def login(request: Request):
    data = await request.json()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not verify_credentials(username, password):
        return JSONResponse(status_code=401, content={"message": "用户名或密码错误"})
    response = JSONResponse(content={"status": "ok", "username": username})
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(username),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/auth/logout")
def logout():
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.post("/api/auth/update")
async def update_auth(request: Request):
    data = await request.json()
    new_username = str(data.get("username") or "").strip()
    new_password = str(data.get("password") or "")
    auth = ensure_auth_config()
    if not new_username:
        return JSONResponse(status_code=400, content={"message": "用户名不能为空"})
    auth["username"] = new_username
    if new_password:
        auth["salt"] = secrets.token_hex(16)
        auth["password_hash"] = hash_password(new_password, str(auth["salt"]))
    auth["session_secret"] = secrets.token_hex(32)
    write_named_config("auth", auth)
    response = JSONResponse(content={"status": "ok", "username": new_username})
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(new_username),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )
    return response


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


@app.get("/api/tasks")
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    type: str = Query("all"),
):
    return db_list_tasks(page, page_size, type)


def run_image_task(internal_task_id: str, settings: dict):
    store = tasks_store[internal_task_id]
    store["status"] = "running"
    store["logs"].append("图片请求已开始。")
    db_mark_started(internal_task_id, store)

    try:
        payload = image_runner.build_payload(settings)
        store["request_payload"] = payload
        db_update_task(internal_task_id, request_payload=payload)
        store["logs"].append("图片请求参数已构建。")
        db_update_task(internal_task_id, logs=store["logs"])
        data = image_runner.create_image(settings, payload)
        store["logs"].append("已收到图片响应。")
        db_update_task(internal_task_id, logs=store["logs"])
        image_bytes, mime_type = image_runner.extract_inline_image(data, proxies=settings.get("proxies"))
        asset = save_image_asset(image_bytes, mime_type, settings)
        store["logs"].append(f"已保存到 downloads/{asset['filename']}。")

        store["status"] = "completed"
        store["image_url"] = asset["url"]
        store["asset"] = asset
        store["thumbnail_url"] = asset.get("thumbnail_url")
        store["raw"] = data
        db_update_task(
            internal_task_id,
            local_url=asset["url"],
            filename=asset["filename"],
            thumbnail_url=asset.get("thumbnail_url"),
            thumbnail_filename=asset.get("thumbnail_filename"),
            raw=data,
            logs=store["logs"],
        )
        db_mark_finished(internal_task_id, store, "completed")
    except Exception as e:
        store["status"] = "error"
        store["error"] = str(e)
        store["logs"].append(f"错误：{str(e)}")
        db_mark_finished(internal_task_id, store, "error")
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
        "request_payload": None,
    }
    db_create_task(internal_task_id, "image", settings)

    background_tasks.add_task(run_image_task, internal_task_id, settings)
    return {"internal_task_id": internal_task_id}

def run_video_task(internal_task_id: str, settings: dict):
    store = tasks_store[internal_task_id]
    store["status"] = "running"
    store["logs"].append("视频请求已开始。")
    db_mark_started(internal_task_id, store)
    
    try:
        payload = video_runner.build_payload(settings)
        store["request_payload"] = payload
        store["logs"].append("视频请求参数已构建。")
        db_update_task(internal_task_id, request_payload=payload, logs=store["logs"])
        create_data = video_runner.create_task(settings, payload)
        api_task_id = video_runner.extract_task_id(create_data)
        store["api_task_id"] = api_task_id
        store["logs"].append(f"远程任务 ID：{api_task_id}")
        db_update_task(internal_task_id, remote_task_id=api_task_id, logs=store["logs"])
        
        result = video_runner.poll_task(settings, api_task_id)
        status = str(result.get("status") or "").strip().lower()
        
        if status != "completed":
            store["status"] = "failed"
            store["raw"] = result
            db_mark_finished(internal_task_id, store, "failed")
            return
             
        media_url = video_runner.resolve_media_url(result)
        store["logs"].append("正在下载视频到本地。")
        db_update_task(internal_task_id, logs=store["logs"])
        asset = save_video_asset(media_url, settings)
        store["logs"].append(f"已保存到 downloads/{asset['filename']}。")
        store["status"] = "completed"
        store["media_url"] = asset["url"]
        store["remote_url"] = media_url
        store["asset"] = asset
        store["thumbnail_url"] = asset.get("thumbnail_url")
        store["raw"] = result
        db_update_task(
            internal_task_id,
            local_url=asset["url"],
            remote_url=media_url,
            filename=asset["filename"],
            thumbnail_url=asset.get("thumbnail_url"),
            thumbnail_filename=asset.get("thumbnail_filename"),
            raw=result,
            logs=store["logs"],
        )
        db_mark_finished(internal_task_id, store, "completed")
        
    except Exception as e:
        store["status"] = "error"
        store["error"] = str(e)
        store["logs"].append(f"错误：{str(e)}")
        db_mark_finished(internal_task_id, store, "error")
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
        "video_reference_field": form_value(form, "video_reference_field") or "video_reference",
        "image_url": remote_urls,
        "image_file": temp_files + local_reference_files,
        "_temp_files": temp_files,
    }
    settings["request_timeout"] = 600
    settings["proxies"] = build_proxies(settings.get("proxy_url", ""))
    settings["poll_interval"] = 10.0
    settings["timeout"] = 600
    settings["reference_format"] = "array"
    settings["reference_field"] = "image_urls"
    settings["extra_field"] = []

    internal_task_id = str(uuid.uuid4())
    tasks_store[internal_task_id] = {
        "type": "video",
        "status": "pending",
        "logs": [],
        "media_url": None,
        "raw": None,
        "error": None,
        "api_task_id": None,
        "request_payload": None
    }
    db_create_task(internal_task_id, "video", settings)
    
    background_tasks.add_task(run_video_task, internal_task_id, settings)
    return {"internal_task_id": internal_task_id}

@app.get("/api/task/{internal_task_id}")
async def get_task_status(internal_task_id: str):
    if internal_task_id in tasks_store:
        return tasks_store[internal_task_id]
    task = db_get_task(internal_task_id)
    if task:
        return task
    if internal_task_id not in tasks_store:
        return JSONResponse(status_code=404, content={"message": "Task not found"})
    return tasks_store[internal_task_id]
