import argparse
import base64
import json
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_BASE_URL = "https://api.pixellelabs.com"
DEFAULT_MODEL = "gemini-3-pro-image-preview"
DEFAULT_PROMPT = "A cinematic mountain sunrise with drifting clouds"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_IMAGE_SIZE = "2K"
DEFAULT_REQUEST_TIMEOUT = 500
DEFAULT_OUT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_NAME = "gemini_image_test_result"
MODEL_OPTIONS = [
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image-preview",
    "gpt-image-2",
]
ASPECT_RATIO_OPTIONS = ["1:1", "4:3", "3:4", "5:4", "4:5", "3:2", "2:3", "16:9", "9:16", "21:9"]
IMAGE_SIZE_OPTIONS = ["1K", "2K", "4K"]
OPENAI_IMAGE_MODELS = {"gpt-image-2"}
OPENAI_QUALITY_OPTIONS = ["low", "medium", "high"]
DEFAULT_OPENAI_QUALITY = "medium"
OPENAI_IMAGE_SIZE_MAP = {
    ("1K", "1:1"): "1024x1024",
    ("1K", "4:3"): "1216x912",
    ("1K", "3:4"): "912x1216",
    ("1K", "5:4"): "1200x960",
    ("1K", "4:5"): "960x1200",
    ("1K", "3:2"): "1296x864",
    ("1K", "2:3"): "864x1296",
    ("1K", "16:9"): "1536x864",
    ("1K", "9:16"): "864x1536",
    ("1K", "21:9"): "1568x672",
    ("2K", "1:1"): "2048x2048",
    ("2K", "4:3"): "2048x1536",
    ("2K", "3:4"): "1536x2048",
    ("2K", "5:4"): "1920x1536",
    ("2K", "4:5"): "1536x1920",
    ("2K", "3:2"): "1920x1280",
    ("2K", "2:3"): "1280x1920",
    ("2K", "16:9"): "2048x1152",
    ("2K", "9:16"): "1152x2048",
    ("2K", "21:9"): "2016x864",
    ("4K", "1:1"): "3840x3840",
    ("4K", "4:3"): "3840x2880",
    ("4K", "3:4"): "2880x3840",
    ("4K", "5:4"): "3840x3072",
    ("4K", "4:5"): "3072x3840",
    ("4K", "3:2"): "3840x2560",
    ("4K", "2:3"): "2560x3840",
    ("4K", "16:9"): "3840x2160",
    ("4K", "9:16"): "2160x3840",
    ("4K", "21:9"): "3584x1536",
}


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def local_file_to_inline_part(file_path: str) -> dict[str, Any]:
    path = Path(file_path)
    if not path.is_file():
        raise RuntimeError(f"image file not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"image file is empty: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(raw).decode("ascii"),
        }
    }


def guess_remote_mime(url: str) -> str | None:
    value = str(url or "").strip()
    if not value:
        return None
    if value.startswith("data:"):
        header = value.split(",", 1)[0]
        mime_type = header[5:].split(";", 1)[0].strip()
        return mime_type or None
    parsed = urlparse(value)
    suffix = Path(parsed.path or "").suffix.lower()
    if not suffix:
        return None
    return mimetypes.types_map.get(suffix)


def url_part(url: str) -> dict[str, Any]:
    file_data: dict[str, Any] = {"fileUri": url}
    mime_type = guess_remote_mime(url)
    if mime_type:
        file_data["mimeType"] = mime_type
    return {"fileData": file_data}


def build_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def is_openai_image_model(model: str) -> bool:
    return str(model or "").strip() in OPENAI_IMAGE_MODELS


def openai_image_size(image_size: str, aspect_ratio: str) -> str:
    size = str(image_size or DEFAULT_IMAGE_SIZE).strip().upper()
    ratio = str(aspect_ratio or DEFAULT_ASPECT_RATIO).strip()
    if "x" in size.lower():
        return size.lower()
    return OPENAI_IMAGE_SIZE_MAP.get((size, ratio), OPENAI_IMAGE_SIZE_MAP[("2K", "16:9")])


def build_payload(settings: dict[str, Any]) -> dict[str, Any]:
    if is_openai_image_model(str(settings.get("model") or "")):
        payload = {
            "model": settings["model"],
            "prompt": settings["prompt"],
            "size": openai_image_size(
                str(settings.get("image_size") or ""),
                str(settings.get("aspect_ratio") or ""),
            ),
            "n": 1,
        }
        quality = str(settings.get("quality") or DEFAULT_OPENAI_QUALITY).strip()
        if quality in OPENAI_QUALITY_OPTIONS:
            payload["quality"] = quality
        if settings.get("image_url") or settings.get("image_file"):
            payload["image_url"] = list(settings.get("image_url") or [])
            payload["image_file"] = list(settings.get("image_file") or [])
        return payload

    parts: list[dict[str, Any]] = [{"text": settings["prompt"]}]
    for item in settings.get("image_url") or []:
        value = str(item).strip()
        if value:
            parts.append(url_part(value))
    for item in settings.get("image_file") or []:
        parts.append(local_file_to_inline_part(str(item)))

    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {
                "aspectRatio": settings["aspect_ratio"],
                "imageSize": settings["image_size"],
            },
        },
    }


def create_image(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if is_openai_image_model(str(settings.get("model") or "")):
        return create_openai_image(settings, payload)

    base_url = str(settings["base_url"] or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/v1beta/models/{settings['model']}:generateContent"
    response = requests.post(
        url,
        headers=build_headers(str(settings.get("api_key") or "")),
        json=payload,
        timeout=int(settings["request_timeout"]),
        proxies=settings.get("proxies"),
    )
    print(f"[POST] {url} -> {response.status_code}")
    data = response.json()
    response.raise_for_status()
    if not isinstance(data, dict):
        raise RuntimeError("expected JSON object response")
    return data


def create_openai_image(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    base_url = str(settings["base_url"] or DEFAULT_BASE_URL).rstrip("/")
    api_key = str(settings.get("api_key") or "")
    timeout = int(settings["request_timeout"])
    proxies = settings.get("proxies")
    image_files = list(payload.get("image_file") or [])
    image_urls = [str(item).strip() for item in payload.get("image_url") or [] if str(item).strip()]

    if image_files or image_urls:
        return create_openai_image_edit(
            base_url, api_key, payload, image_files, image_urls, timeout, proxies
        )

    url = f"{base_url}/v1/images/generations"
    request_payload = {
        "model": payload["model"],
        "prompt": payload["prompt"],
        "size": payload["size"],
        "n": payload.get("n", 1),
    }
    if payload.get("quality"):
        request_payload["quality"] = payload["quality"]
    response = requests.post(
        url,
        headers=build_headers(api_key),
        json=request_payload,
        timeout=timeout,
        proxies=proxies,
    )
    print(f"[POST] {url} -> {response.status_code}")
    data = response.json()
    response.raise_for_status()
    if not isinstance(data, dict):
        raise RuntimeError("expected JSON object response")
    return data


def create_openai_image_edit(
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
    image_files: list[str],
    image_urls: list[str],
    timeout: int,
    proxies: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{base_url}/v1/images/edits"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = {
        "model": payload["model"],
        "prompt": payload["prompt"],
        "size": payload["size"],
        "n": str(payload.get("n", 1)),
    }
    if payload.get("quality"):
        data["quality"] = payload["quality"]
    handles = []
    temp_paths = []

    try:
        for image_url in image_urls:
            response = requests.get(image_url, timeout=timeout, proxies=proxies)
            response.raise_for_status()
            suffix = Path(urlparse(image_url).path or "").suffix or ".png"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(response.content)
            tmp.close()
            temp_paths.append(tmp.name)
            image_files.append(tmp.name)

        files = []
        for file_path in image_files:
            path = Path(file_path)
            if not path.is_file():
                raise RuntimeError(f"image file not found: {path}")
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            handle = path.open("rb")
            handles.append(handle)
            files.append(("image", (path.name, handle, mime_type)))

        response = requests.post(
            url,
            headers=headers,
            data=data,
            files=files,
            timeout=timeout,
            proxies=proxies,
        )
        print(f"[POST] {url} -> {response.status_code}")
        result = response.json()
        response.raise_for_status()
        if not isinstance(result, dict):
            raise RuntimeError("expected JSON object response")
        return result
    finally:
        for handle in handles:
            handle.close()
        for temp_path in temp_paths:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


def extract_inline_image(data: dict[str, Any], proxies: dict[str, str] | None = None) -> tuple[bytes, str]:
    image_data = data.get("data")
    if isinstance(image_data, list):
        for item in image_data:
            if not isinstance(item, dict):
                continue
            raw_b64 = str(item.get("b64_json") or "").strip()
            if raw_b64:
                return base64.b64decode(raw_b64), "image/png"
            image_url = str(item.get("url") or "").strip()
            if image_url:
                response = requests.get(image_url, timeout=DEFAULT_REQUEST_TIMEOUT, proxies=proxies)
                response.raise_for_status()
                mime_type = response.headers.get("Content-Type", "image/png").split(";", 1)[0]
                return response.content, mime_type or "image/png"

    candidates = data.get("candidates") or []
    for candidate in candidates:
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        for part in parts:
            inline_data = part.get("inlineData") or {}
            mime_type = (
                str(inline_data.get("mimeType") or "image/png").strip() or "image/png"
            )
            raw_b64 = str(inline_data.get("data") or "").strip()
            if raw_b64:
                return base64.b64decode(raw_b64), mime_type
    raise RuntimeError("response does not contain inlineData image")


def choose_suffix(mime_type: str) -> str:
    lowered = str(mime_type or "").lower()
    if "png" in lowered:
        return ".png"
    if "jpeg" in lowered or "jpg" in lowered:
        return ".jpg"
    if "webp" in lowered:
        return ".webp"
    return ".bin"


def save_image_bytes(
    image_bytes: bytes, mime_type: str, out_dir: str, output_name: str
) -> Path:
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{output_name}{choose_suffix(mime_type)}"
    file_path.write_bytes(image_bytes)
    return file_path

