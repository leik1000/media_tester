import unittest
from unittest.mock import mock_open, patch

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
    closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

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

    def test_gpt_image_2_5_models_use_openai_generation_and_quality(self):
        for model in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            with self.subTest(model=model):
                payload = image_runner.build_payload(
                    _image_settings(
                        model=model,
                        aspect_ratio="16:9",
                        image_size="2K",
                        quality="high",
                    )
                )

                self.assertTrue(image_runner.is_openai_image_model(model))
                self.assertTrue(image_runner.supports_openai_quality(model))
                self.assertEqual(payload["model"], model)
                self.assertEqual(payload["size"], "2048x1152")
                self.assertEqual(payload["quality"], "high")
                self.assertNotIn("aspect_ratio", payload)

    def test_gpt_image_2_5_unknown_quality_defaults_to_high(self):
        for model in ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            for quality in ("xhigh", "max", "unsupported"):
                with self.subTest(model=model, quality=quality):
                    payload = image_runner.build_payload(
                        _image_settings(model=model, quality=quality)
                    )
                    self.assertEqual(payload["quality"], "high")

    def test_gpt_image_2_unknown_quality_keeps_existing_behavior(self):
        payload = image_runner.build_payload(
            _image_settings(model="gpt-image-2", quality="xhigh")
        )

        self.assertNotIn("quality", payload)

    def test_max_models_send_all_quality_levels_in_generation_requests(self):
        for model in ("gpt-image-2.5-flare-max", "gpt-image-2.5-sunburst-max"):
            self.assertIn(model, image_runner.MODEL_OPTIONS)
            self.assertTrue(image_runner.supports_openai_quality(model))
            for quality in ("low", "medium", "high", "xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    settings = _image_settings(model=model, quality=quality)
                    payload = image_runner.build_payload(settings)
                    with patch.object(image_runner.requests, "post", return_value=_Response()) as post:
                        image_runner.create_image(settings, payload)
                    args, kwargs = post.call_args
                    self.assertEqual(args[0], "https://api.example.test/v1/images/generations")
                    self.assertEqual(kwargs["json"], {
                        "model": model,
                        "prompt": "test prompt",
                        "size": "2048x1152",
                        "n": 1,
                        "quality": quality,
                    })
                    self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_max_models_normalize_quality_like_luma(self):
        cases = ((None, "medium"), ("", "medium"), ("auto", "medium"),
                 ("unsupported", "medium"), (" XHIGH ", "xhigh"), (" MAX ", "max"))
        for model in ("gpt-image-2.5-flare-max", "gpt-image-2.5-sunburst-max"):
            for quality, expected in cases:
                with self.subTest(model=model, quality=quality):
                    payload = image_runner.build_payload(_image_settings(model=model, quality=quality))
                    self.assertEqual(payload["quality"], expected)

    def test_max_models_preserve_extended_quality_in_multipart_edits(self):
        for model in ("gpt-image-2.5-flare-max", "gpt-image-2.5-sunburst-max"):
            for quality in ("xhigh", "max"):
                with self.subTest(model=model, quality=quality):
                    settings = _image_settings(model=model, quality=quality, image_file=["reference.png"])
                    payload = image_runner.build_payload(settings)
                    opened = mock_open(read_data=b"image bytes")
                    with (
                        patch.object(image_runner.Path, "is_file", return_value=True),
                        patch.object(image_runner.Path, "open", opened),
                        patch.object(image_runner.requests, "post", return_value=_Response()) as post,
                    ):
                        image_runner.create_image(settings, payload)
                    args, kwargs = post.call_args
                    self.assertEqual(args[0], "https://api.example.test/v1/images/edits")
                    self.assertEqual(kwargs["data"], {
                        "model": model,
                        "prompt": "test prompt",
                        "size": "2048x1152",
                        "n": "1",
                        "quality": quality,
                    })
                    self.assertNotIn("json", kwargs)
                    self.assertEqual(kwargs["files"][0][0], "image")
                    self.assertEqual(kwargs["files"][0][1][0], "reference.png")
                    opened().close.assert_called_once()

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

        response = _Response()
        with patch.object(image_runner.requests, "post", return_value=response) as post:
            image_runner.create_openai_image(_image_settings(model="seedream-5-pro"), payload)
        self.assertTrue(response.closed)

        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://api.example.test/v1/images/generations")
        self.assertEqual(kwargs["json"]["aspect_ratio"], "16:9")
        self.assertEqual(kwargs["json"]["size"], "1K")
        self.assertNotIn("quality", kwargs["json"])


if __name__ == "__main__":
    unittest.main()
