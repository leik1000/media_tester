import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app as media_app
from tests import run_video_availability_check as video_runner


def _sora_settings(**overrides):
    settings = {
        "model": "sora-v3-pro",
        "prompt": "test prompt",
        "aspect_ratio": "16:9",
        "size": None,
        "duration": 8,
        "seconds": None,
        "resolution": "720p",
        "start_frame": "legacy-start",
        "end_frame": "legacy-end",
        "video_reference": "https://example.com/legacy.mp4",
        "video_reference_field": "video_reference",
        "image_url": ["https://example.com/image.png"],
        "image_file": [],
        "video_url": [
            "https://example.com/video-1.mp4",
            "https://example.com/video-2.mp4",
            "https://example.com/video-3.mp4",
        ],
        "audio_url": [
            "https://example.com/audio-1.mp3",
            "https://example.com/audio-2.mp3",
            "https://example.com/audio-3.mp3",
        ],
        "generate_audio": True,
        "extra_field": [],
    }
    settings.update(overrides)
    return settings


class VideoPayloadTests(unittest.TestCase):
    def test_sora_v3_uses_url_arrays_without_legacy_fields(self):
        payload = video_runner.build_payload(_sora_settings())

        self.assertEqual(payload["image_urls"], ["https://example.com/image.png"])
        self.assertEqual(len(payload["video_urls"]), 3)
        self.assertEqual(len(payload["audio_urls"]), 3)
        self.assertTrue(payload["generate_audio"])
        self.assertNotIn("start_frame", payload)
        self.assertNotIn("end_frame", payload)
        self.assertNotIn("video_reference", payload)
        self.assertFalse(
            any(
                str(url).startswith("data:")
                for key in ("image_urls", "video_urls", "audio_urls")
                for url in payload.get(key, [])
            )
        )

    def test_prompt_is_not_length_limited(self):
        prompt = "x" * 5000
        payload = video_runner.build_payload(_sora_settings(prompt=prompt))
        self.assertEqual(payload["prompt"], prompt)

    def test_non_sora_model_keeps_legacy_video_reference(self):
        payload = video_runner.build_payload(
            _sora_settings(
                model="gemini-omni-flash",
                video_url=[],
                audio_url=[],
            )
        )
        self.assertEqual(payload["video_reference"], "https://example.com/legacy.mp4")
        self.assertEqual(payload["start_frame"], "legacy-start")
        self.assertNotIn("video_urls", payload)
        self.assertNotIn("audio_urls", payload)


class SoraValidationTests(unittest.TestCase):
    def test_accepts_documented_reference_limits(self):
        media_app.validate_sora_v3_video_request(
            model="sora-v3-fast",
            aspect_ratio="21:9",
            resolution="720p",
            duration=15,
            image_count=6,
            video_count=3,
            audio_count=3,
        )

    def test_rejects_four_reference_videos(self):
        with self.assertRaisesRegex(HTTPException, "最多支持 3 个参考视频"):
            media_app.validate_sora_v3_video_request(
                model="sora-v3-pro",
                aspect_ratio="16:9",
                resolution="720p",
                duration=8,
                image_count=1,
                video_count=4,
                audio_count=0,
            )

    def test_rejects_four_reference_audios(self):
        with self.assertRaisesRegex(HTTPException, "最多支持 3 个参考音频"):
            media_app.validate_sora_v3_video_request(
                model="sora-v3-pro",
                aspect_ratio="16:9",
                resolution="720p",
                duration=8,
                image_count=1,
                video_count=0,
                audio_count=4,
            )

    def test_rejects_audio_without_image_or_video(self):
        with self.assertRaisesRegex(HTTPException, "必须搭配"):
            media_app.validate_sora_v3_video_request(
                model="sora-v3-pro",
                aspect_ratio="16:9",
                resolution="720p",
                duration=8,
                image_count=0,
                video_count=0,
                audio_count=1,
            )


class _Upload:
    def __init__(self, filename: str, content_type: str, content: bytes):
        self.filename = filename
        self.content_type = content_type
        self._content = content
        self._read = False

    async def read(self, _size: int):
        if self._read:
            return b""
        self._read = True
        return self._content


class _Form:
    def __init__(self, values):
        self.values = values

    def getlist(self, name):
        return list(self.values.get(name) or [])


class ReferenceUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_video_is_published_as_full_url(self):
        form = _Form(
            {
                "video_file": [
                    _Upload("reference.mp4", "video/mp4", b"video-bytes")
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            download_dir = Path(temp_dir)
            reference_dir = download_dir / "reference-media"
            reference_dir.mkdir()
            with (
                patch.object(media_app, "DOWNLOAD_DIR", download_dir),
                patch.object(media_app, "REFERENCE_MEDIA_DIR", reference_dir),
            ):
                paths, urls = await media_app.save_reference_uploads(
                    form,
                    "video_file",
                    "video",
                    "https://tester.example.com/",
                )

            self.assertEqual(len(paths), 1)
            self.assertEqual(len(urls), 1)
            self.assertTrue(
                urls[0].startswith(
                    "https://tester.example.com/downloads/reference-media/"
                )
            )
            self.assertTrue(urls[0].endswith(".mp4"))
            self.assertEqual(Path(paths[0]).read_bytes(), b"video-bytes")

    async def test_invalid_public_base_url_is_rejected(self):
        form = _Form(
            {"audio_file": [_Upload("reference.mp3", "audio/mpeg", b"audio")]}
        )
        with self.assertRaisesRegex(HTTPException, "公网素材地址"):
            await media_app.save_reference_uploads(
                form,
                "audio_file",
                "audio",
                "http://",
            )


class ExistingReferenceTests(unittest.TestCase):
    def test_generated_file_uses_original_download_without_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            download_dir = Path(temp_dir)
            source = download_dir / "image_original.png"
            source.write_bytes(b"image-bytes")

            with patch.object(media_app, "DOWNLOAD_DIR", download_dir):
                remote_urls, local_files = media_app.split_reference_urls(
                    ["/downloads/image_original.png"]
                )
                urls = media_app.public_reference_file_urls(
                    local_files, "http://tester.example.com:5800"
                )

            self.assertEqual(remote_urls, [])
            self.assertEqual(local_files, [str(source.resolve())])
            self.assertEqual(
                urls,
                ["http://tester.example.com:5800/downloads/image_original.png"],
            )
            self.assertEqual(list(download_dir.iterdir()), [source])


if __name__ == "__main__":
    unittest.main()
