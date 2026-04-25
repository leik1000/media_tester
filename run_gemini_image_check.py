import argparse
import base64
import json
import mimetypes
import os
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
ASPECT_RATIO_OPTIONS = ["1:1", "4:3", "3:4", "16:9", "9:16"]
IMAGE_SIZE_OPTIONS = ["1K", "2K", "4K"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Gemini image test and decode inline base64 image response"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=os.getenv("VIDEO_TEST_API_KEY", ""))
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=MODEL_OPTIONS)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--aspect-ratio", default=DEFAULT_ASPECT_RATIO)
    parser.add_argument("--image-size", default=DEFAULT_IMAGE_SIZE)
    parser.add_argument("--image-url", action="append", default=None)
    parser.add_argument("--image-file", action="append", default=None)
    parser.add_argument("--request-timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--output-name", default=DEFAULT_OUTPUT_NAME)
    parser.add_argument("--save-image", action="store_true")
    parser.add_argument("--print-payload", action="store_true")
    return parser.parse_args()


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


def build_payload(settings: dict[str, Any]) -> dict[str, Any]:
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
    base_url = str(settings["base_url"] or DEFAULT_BASE_URL).rstrip("/")
    url = f"{base_url}/v1beta/models/{settings['model']}:generateContent"
    response = requests.post(
        url,
        headers=build_headers(str(settings.get("api_key") or "")),
        json=payload,
        timeout=int(settings["request_timeout"]),
    )
    print(f"[POST] {url} -> {response.status_code}")
    data = response.json()
    response.raise_for_status()
    if not isinstance(data, dict):
        raise RuntimeError("expected JSON object response")
    return data


def extract_inline_image(data: dict[str, Any]) -> tuple[bytes, str]:
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


def main() -> int:
    args = parse_args()
    settings = {
        "base_url": args.base_url,
        "api_key": args.api_key,
        "model": args.model,
        "prompt": args.prompt,
        "aspect_ratio": args.aspect_ratio,
        "image_size": args.image_size,
        "image_url": list(args.image_url or []),
        "image_file": list(args.image_file or []),
        "request_timeout": args.request_timeout,
        "out_dir": args.out_dir,
        "output_name": args.output_name,
    }
    try:
        payload = build_payload(settings)
        if args.print_payload:
            print("[payload]")
            print(pretty(payload))
        data = create_image(settings, payload)
        image_bytes, mime_type = extract_inline_image(data)
        print(f"[result] mime_type={mime_type} bytes={len(image_bytes)}")
        if args.save_image:
            path = save_image_bytes(
                image_bytes, mime_type, args.out_dir, args.output_name
            )
            print(f"[result] saved={path}")
        return 0
    except Exception as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
