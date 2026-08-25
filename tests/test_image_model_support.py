import unittest
from unittest.mock import patch

from tests import run_gemini_image_check as image_runner


def _image_settings(**overrides):
    settings = {
        "base_url": "https://api.example.test",
        "api_key": "test-key",
        "model": "gemini-3-pro-image-preview",
        "prompt": "test prompt",
        "aspect_ratio": "16:9",
        "image_size": "2K",
        "quality": "medium",
        "image_url": [],
        "image_file": [],
        "request_timeout": 30,
        "proxies": None,
    }
    settings.update(overrides)
    return settings


class _Response:
    status_code = 200
    text = "{}"

    def json(self):
        return {"data": []}


class ImagePayloadTests(unittest.TestCase):
    def test_seedream_uses_resolution_size_and_aspect_ratio_without_quality(self):
        payload = image_runner.build_payload(
            _image_settings(
                model="seedream-5-pro",
                aspect_ratio="9:21",
                image_size="2K",
                quality="high",
            )
        )

        self.assertTrue(image_runner.is_openai_image_model("seedream-5-pro"))
        self.assertFalse(image_runner.supports_openai_quality("seedream-5-pro"))
        self.assertEqual(payload["model"], "seedream-5-pro")
        self.assertEqual(payload["size"], "2K")
        self.assertEqual(payload["aspect_ratio"], "9:21")
        self.assertNotIn("quality", payload)

    def test_gpt_image_2_keeps_pixel_size_mapping_and_quality(self):
        payload = image_runner.build_payload(
            _image_settings(
                model="gpt-image-2",
                aspect_ratio="16:9",
                image_size="2K",
                quality="high",
            )
        )

        self.assertEqual(payload["size"], "2048x1152")
        self.assertEqual(payload["quality"], "high")
        self.assertNotIn("aspect_ratio", payload)

    def test_gemini_lite_uses_generate_content_image_config(self):
        payload = image_runner.build_payload(
            _image_settings(
                model="gemini-3.1-flash-lite-image",
                aspect_ratio="8:1",
                image_size="1K",
            )
        )

        self.assertFalse(image_runner.is_openai_image_model("gemini-3.1-flash-lite-image"))
        image_config = payload["generationConfig"]["imageConfig"]
        self.assertEqual(payload["contents"][0]["parts"][0], {"text": "test prompt"})
        self.assertEqual(image_config["aspectRatio"], "8:1")
        self.assertEqual(image_config["imageSize"], "1K")

    def test_seedream_generation_request_includes_aspect_ratio(self):
        payload = image_runner.build_payload(
            _image_settings(
                model="seedream-5-pro",
                aspect_ratio="16:9",
                image_size="1K",
                quality="high",
            )
        )

        with patch.object(image_runner.requests, "post", return_value=_Response()) as post:
            image_runner.create_openai_image(_image_settings(model="seedream-5-pro"), payload)

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.example.test/v1/images/generations")
        self.assertEqual(kwargs["json"]["aspect_ratio"], "16:9")
        self.assertEqual(kwargs["json"]["size"], "1K")
        self.assertNotIn("quality", kwargs["json"])


if __name__ == "__main__":
    unittest.main()
