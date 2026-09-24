import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from PIL import Image

import app as media_app


class ReferenceImageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, path in (
            ("DOWNLOAD_DIR", self.root),
            ("REFERENCE_MEDIA_DIR", self.root / "reference-media"),
            ("THUMB_DIR", self.root / "thumbs"),
        ):
            path.mkdir(exist_ok=True)
            patcher = patch.object(media_app, name, path)
            patcher.start()
            self.addCleanup(patcher.stop)
        output = io.BytesIO()
        Image.new("RGB", (900, 600), "blue").save(output, "PNG")
        self.image = output.getvalue()

    def test_upload_preserves_original_and_generates_thumbnail(self):
        result = media_app.store_reference_image([self.image], "test.png")
        original = self.root / result["url"].removeprefix("/downloads/")
        thumbnail = self.root / result["thumbnailUrl"].removeprefix("/downloads/")
        self.assertEqual(original.read_bytes(), self.image)
        with Image.open(thumbnail) as image:
            self.assertEqual(image.size, (480, 270))
        self.assertEqual(result["name"], "test.png")

    def test_invalid_and_oversized_files_leave_no_partial_files(self):
        for chunks, limit in (([b"not an image"], 100), ([self.image], 10)):
            with patch.object(media_app, "REFERENCE_IMAGE_MAX_BYTES", limit):
                with self.assertRaises(HTTPException):
                    media_app.store_reference_image(chunks, "bad.png")
            self.assertEqual(list((self.root / "reference-media").iterdir()), [])
            self.assertEqual(list((self.root / "thumbs").iterdir()), [])

    def test_url_download_is_streamed_and_stored_locally(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [self.image[:20], self.image[20:]]
        with patch.object(media_app.requests, "get", return_value=response) as get:
            result = media_app.import_reference_image_url("https://example.test/image.png")
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertTrue(result["url"].startswith("/downloads/reference-media/"))
        original = self.root / result["url"].removeprefix("/downloads/")
        self.assertEqual(original.read_bytes(), self.image)

    def test_gallery_reference_does_not_download_or_replace_original(self):
        (self.root / "gallery.png").write_bytes(self.image)
        with patch.object(media_app.requests, "get") as get:
            result = media_app.import_reference_image_url("/downloads/gallery.png")
        get.assert_not_called()
        self.assertEqual(result["url"], "/downloads/gallery.png")
        self.assertEqual((self.root / "gallery.png").read_bytes(), self.image)

    def test_missing_gallery_and_invalid_urls_are_rejected(self):
        for url in ("/downloads/missing.png", "file:///etc/passwd", "invalid"):
            with self.subTest(url=url), self.assertRaises(HTTPException):
                media_app.import_reference_image_url(url)


if __name__ == "__main__":
    unittest.main()
