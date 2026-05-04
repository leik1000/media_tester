import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests


ENV_PREFIX = "VIDEO_TEST_"
DEFAULT_CONFIG_PATH = ""
DEFAULT_BASE_URL = "https://api.pixellelabs.com"
DEFAULT_CREATE_PATH = "/v1/videos"
DEFAULT_STATUS_PATH = "/v1/videos/{task_id}"
DEFAULT_MODEL = "sora2"
DEFAULT_PROMPT = "A cinematic hummingbird flying through a sunlit garden"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_DURATION = 4
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_POLL_INTERVAL = 10.0
DEFAULT_REQUEST_TIMEOUT = 600
DEFAULT_OUT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_NAME = "video_test_result"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create, poll, and optionally download a video task for endpoint availability checks"
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH, help="JSON config file path"
    )
    parser.add_argument("--base-url", help="API base URL")
    parser.add_argument("--api-key", help="Bearer API key")
    parser.add_argument("--create-path", help="Create-task path")
    parser.add_argument("--status-path", help="Status path, supports {task_id}")
    parser.add_argument("--model", help="Video model")
    parser.add_argument("--prompt", help="Prompt text")
    parser.add_argument("--aspect-ratio", help="Aspect ratio, for example 16:9")
    parser.add_argument("--size", help="Optional size, for example 1280x720")
    parser.add_argument("--duration", type=int, help="Duration in seconds")
    parser.add_argument("--seconds", type=int, help="Alias for duration")
    parser.add_argument("--resolution", help="Optional resolution, for example 1080p")
    parser.add_argument(
        "--image-url",
        action="append",
        default=None,
        help="Reference image URL or data URL, repeatable",
    )
    parser.add_argument(
        "--image-file",
        action="append",
        default=None,
        help="Local image file, repeatable",
    )
    parser.add_argument(
        "--reference-field",
        choices=["image_urls", "input_reference"],
        help="Request field used for reference images",
    )
    parser.add_argument(
        "--reference-format",
        choices=["string", "array"],
        help="Serialize reference value as string or array",
    )
    parser.add_argument(
        "--extra-field",
        action="append",
        default=None,
        metavar="KEY=JSON",
        help="Additional payload field, value must be valid JSON",
    )
    parser.add_argument("--timeout", type=int, help="Total poll timeout in seconds")
    parser.add_argument("--poll-interval", type=float, help="Poll interval seconds")
    parser.add_argument(
        "--request-timeout", type=int, help="Single request timeout seconds"
    )
    parser.add_argument("--out-dir", help="Download directory")
    parser.add_argument("--output-name", help="Downloaded file name without suffix")
    parser.add_argument(
        "--download-check",
        action="store_true",
        default=None,
        help="Download final media to verify the returned URL",
    )
    parser.add_argument(
        "--skip-head-check",
        action="store_true",
        default=None,
        help="Skip HEAD request against final media URL",
    )
    parser.add_argument(
        "--print-payload",
        action="store_true",
        default=None,
        help="Print final request payload before sending",
    )
    return parser.parse_args()


def env_key(name: str) -> str:
    return f"{ENV_PREFIX}{name.upper()}"


def read_json_file(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    if not path.is_file():
        raise RuntimeError(f"config file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("config file must contain a JSON object")
    return data


def parse_json_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON value: {raw}") from exc


def local_file_to_data_url(file_path: str) -> str:
    path = Path(file_path)
    if not path.is_file():
        raise RuntimeError(f"image file not found: {path}")
    raw = path.read_bytes()
    if not raw:
        raise RuntimeError(f"image file is empty: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def coerce_config_value(key: str, value: str) -> Any:
    if key in {"duration", "seconds", "timeout", "request_timeout"}:
        return int(value)
    if key == "poll_interval":
        return float(value)
    if key in {"download_check", "skip_head_check", "print_payload"}:
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if key in {"image_url", "image_file", "extra_field"}:
        try:
            parsed = parse_json_value(value)
        except RuntimeError:
            return [value]
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict) and key == "extra_field":
            return [
                f"{field}={json.dumps(raw_value, ensure_ascii=False)}"
                for field, raw_value in parsed.items()
            ]
        return [str(parsed)]
    return value


def build_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "base_url": DEFAULT_BASE_URL,
        "api_key": "",
        "create_path": DEFAULT_CREATE_PATH,
        "status_path": DEFAULT_STATUS_PATH,
        "model": DEFAULT_MODEL,
        "prompt": DEFAULT_PROMPT,
        "aspect_ratio": DEFAULT_ASPECT_RATIO,
        "size": None,
        "duration": DEFAULT_DURATION,
        "seconds": None,
        "resolution": None,
        "image_url": [],
        "image_file": [],
        "reference_field": "input_reference",
        "reference_format": "array",
        "extra_field": [],
        "timeout": DEFAULT_TIMEOUT_SECONDS,
        "poll_interval": DEFAULT_POLL_INTERVAL,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
        "out_dir": str(DEFAULT_OUT_DIR),
        "output_name": DEFAULT_OUTPUT_NAME,
        "download_check": False,
        "skip_head_check": False,
        "print_payload": False,
    }

    if args.config:
        settings.update(read_json_file(args.config))

    for key in list(settings.keys()):
        raw = os.getenv(env_key(key))
        if raw is not None and raw != "":
            settings[key] = coerce_config_value(key, raw)

    for key, value in vars(args).items():
        if key == "config" or value is None:
            continue
        settings[key] = value

    settings["base_url"] = str(settings["base_url"] or DEFAULT_BASE_URL).rstrip("/")
    settings["create_path"] = ensure_path(settings["create_path"], DEFAULT_CREATE_PATH)
    settings["status_path"] = ensure_path(settings["status_path"], DEFAULT_STATUS_PATH)
    settings["out_dir"] = str(settings["out_dir"] or DEFAULT_OUT_DIR)
    settings["output_name"] = str(
        settings["output_name"] or DEFAULT_OUTPUT_NAME
    ).strip()
    settings["image_url"] = list(settings.get("image_url") or [])
    settings["image_file"] = list(settings.get("image_file") or [])
    extra_field = settings.get("extra_field") or []
    if isinstance(extra_field, dict):
        settings["extra_field"] = [
            f"{field}={json.dumps(raw_value, ensure_ascii=False)}"
            for field, raw_value in extra_field.items()
        ]
    else:
        settings["extra_field"] = list(extra_field)
    return settings


def ensure_path(value: Any, default: str) -> str:
    path_value = str(value or default).strip()
    if not path_value.startswith("/"):
        path_value = f"/{path_value}"
    return path_value


def pretty(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def parse_extra_fields(items: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in items:
        key, sep, raw_value = str(item).partition("=")
        if not sep:
            raise RuntimeError(f"invalid --extra-field value: {item}")
        field_name = key.strip()
        if not field_name:
            raise RuntimeError(f"empty field name in --extra-field: {item}")
        payload[field_name] = parse_json_value(raw_value)
    return payload


def resolve_reference_images(settings: dict[str, Any]) -> list[str]:
    values = [str(item).strip() for item in settings["image_url"] if str(item).strip()]
    for file_path in settings["image_file"]:
        values.append(local_file_to_data_url(str(file_path)))
    return values


def build_payload(settings: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings["model"],
        "prompt": settings["prompt"],
    }
    if settings.get("aspect_ratio"):
        payload["aspect_ratio"] = settings["aspect_ratio"]
    if settings.get("size"):
        payload["size"] = settings["size"]
    if settings.get("duration") is not None:
        payload["duration"] = settings["duration"]
    if settings.get("seconds") is not None:
        payload["seconds"] = settings["seconds"]
    if settings.get("resolution"):
        payload["resolution"] = settings["resolution"]
    if settings.get("start_frame"):
        payload["start_frame"] = settings["start_frame"]
    if settings.get("end_frame"):
        payload["end_frame"] = settings["end_frame"]
    if settings.get("video_reference"):
        payload["video_reference"] = settings["video_reference"]

    references = resolve_reference_images(settings)
    if references:
        if settings["reference_format"] == "string":
            if len(references) != 1:
                raise RuntimeError(
                    "reference_format=string requires exactly one reference image"
                )
            payload[settings["reference_field"]] = references[0]
        else:
            payload[settings["reference_field"]] = references

    payload.update(parse_extra_fields(settings["extra_field"]))
    return payload


def request_json(
    method: str, url: str, *, headers: dict[str, str], timeout: int, **kwargs: Any
) -> tuple[requests.Response, dict[str, Any]]:
    response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    print(f"[{method}] {url} -> {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        snippet = response.text[:500]
        raise RuntimeError(f"non-JSON response from {url}: {snippet}")
    response.raise_for_status()
    if not isinstance(data, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    return response, data


def create_task(settings: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{settings['base_url']}{settings['create_path']}"
    _, data = request_json(
        "POST",
        url,
        headers=build_headers(str(settings.get("api_key") or "")),
        timeout=int(settings["request_timeout"]),
        proxies=settings.get("proxies"),
        json=payload,
    )
    return data


def extract_task_id(data: dict[str, Any]) -> str:
    for key in ("task_id", "id"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    raise RuntimeError("missing task_id in response")


def poll_task(settings: dict[str, Any], task_id: str) -> dict[str, Any]:
    started_at = time.time()
    poll_count = 0
    headers = build_headers(str(settings.get("api_key") or ""))
    while True:
        poll_count += 1
        status_path = str(settings["status_path"]).format(task_id=task_id)
        url = f"{settings['base_url']}{status_path}"
        _, data = request_json(
            "GET",
            url,
            headers=headers,
            timeout=int(settings["request_timeout"]),
            proxies=settings.get("proxies"),
        )
        status = str(data.get("status") or "").strip().lower()
        print(f"[poll #{poll_count}] status={status or 'unknown'}")
        if status in {"completed", "failed"}:
            return data
        if time.time() - started_at > int(settings["timeout"]):
            raise RuntimeError(f"task {task_id} timed out after {settings['timeout']}s")
        time.sleep(float(settings["poll_interval"]))


def resolve_media_url(data: dict[str, Any]) -> str:
    for key in ("video_url", "image_url", "url"):
        value = str(data.get(key) or "").strip()
        if value:
            return value
    result = data.get("result")
    if isinstance(result, dict):
        for key in ("video_url", "image_url", "url"):
            value = str(result.get(key) or "").strip()
            if value:
                return value
    raise RuntimeError("completed task did not return a media url")


def head_check_media(url: str, timeout: int, proxies: dict[str, str] | None = None) -> None:
    try:
        response = requests.head(url, allow_redirects=True, timeout=timeout, proxies=proxies)
        print(f"[HEAD] {url} -> {response.status_code}")
        if response.status_code >= 400:
            raise RuntimeError(f"media HEAD check failed: {response.status_code}")
    except requests.RequestException as exc:
        raise RuntimeError(f"media HEAD check failed: {exc}") from exc


def choose_suffix(content_type: str, media_url: str) -> str:
    lowered = content_type.lower()
    if "mp4" in lowered or media_url.lower().endswith(".mp4"):
        return ".mp4"
    if "quicktime" in lowered or media_url.lower().endswith(".mov"):
        return ".mov"
    if "webm" in lowered or media_url.lower().endswith(".webm"):
        return ".webm"
    if "image" in lowered:
        return ".png"
    return ".bin"


def download_media(url: str, out_dir: str, output_name: str, timeout: int, proxies: dict[str, str] | None = None) -> Path:
    response = requests.get(url, allow_redirects=True, timeout=timeout, proxies=proxies)
    print(f"[DOWNLOAD] {url} -> {response.status_code}")
    response.raise_for_status()
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = choose_suffix(str(response.headers.get("content-type") or ""), url)
    file_path = target_dir / f"{output_name}{suffix}"
    file_path.write_bytes(response.content)
    return file_path

