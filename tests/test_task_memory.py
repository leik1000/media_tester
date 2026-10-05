import json
import sqlite3
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app as media_app


class TaskMemoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name, value in (
            ("CONFIG_DB_PATH", Path(directory.name) / "config.db"),
            ("DOWNLOAD_DIR", Path(directory.name)),
            ("tasks_store", {}),
        ):
            patcher = patch.object(media_app, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        media_app.init_config_db()
        self.settings = {"model": "test", "prompt": "test", "request_timeout": 10}

    def create_task(self, task_id, kind):
        media_app.db_create_task(task_id, kind, self.settings)
        media_app.tasks_store[task_id] = {"type": kind, "status": "pending", "logs": []}

    def test_repeated_images_release_cache_and_do_not_persist_base64(self):
        payload = {"contents": [{"parts": [{"inlineData": {
            "mimeType": "image/png", "data": "A" * 1_000_000,
        }}]}]}
        response = {"data": [{"b64_json": "B" * 2_000_000}], "usage": {"total_tokens": 42}}
        asset = {"url": "/downloads/test.png", "filename": "test.png"}
        with patch.object(media_app.image_runner, "build_payload", return_value=payload), \
             patch.object(media_app.image_runner, "create_image", return_value=response) as create, \
             patch.object(media_app.image_runner, "extract_inline_image", return_value=(b"png", "image/png")), \
             patch.object(media_app, "save_image_asset", return_value=asset):
            for i in range(30):
                task_id = str(i)
                self.create_task(task_id, "image")
                media_app.run_image_task(task_id, self.settings)
                self.assertEqual(media_app.tasks_store, {})
                task = media_app.get_task_status(task_id)
                self.assertEqual(task["status"], "completed")
                self.assertEqual(task["image_url"], asset["url"])
            self.assertIs(create.call_args.args[1], payload)
        # The upstream data must remain intact; only diagnostics are summarized.
        self.assertEqual(len(response["data"][0]["b64_json"]), 2_000_000)
        with media_app.config_connection() as conn:
            sizes = conn.execute("SELECT length(raw), length(request_payload) FROM media_tasks").fetchall()
        self.assertTrue(all(raw < 1000 and request < 1000 for raw, request in sizes))
        self.assertEqual(media_app.db_get_task("29")["raw"]["usage"]["total_tokens"], 42)

    def test_image_error_evicts_cache_and_cleans_temp_files(self):
        self.create_task("error", "image")
        with patch.object(media_app.image_runner, "build_payload", side_effect=RuntimeError("upstream failed")), \
             patch.object(media_app, "cleanup_files") as cleanup:
            media_app.run_image_task("error", self.settings)
        cleanup.assert_called_once_with([])
        self.assertEqual(media_app.tasks_store, {})
        task = media_app.get_task_status("error")
        self.assertEqual(task["status"], "error")
        self.assertEqual(task["error"], "upstream failed")

    def test_large_response_allocations_do_not_accumulate(self):
        # Use plain fakes: MagicMock call history would itself retain responses.
        with patch.object(media_app.image_runner, "build_payload", lambda settings: {}), \
             patch.object(media_app.image_runner, "create_image", lambda settings, payload: {
                 "data": [{"b64_json": "A" * 4_000_000}]
             }), \
             patch.object(media_app.image_runner, "extract_inline_image", lambda data, **kwargs: (b"png", "image/png")), \
             patch.object(media_app, "save_image_asset", lambda *args: {
                 "url": "/downloads/test.png", "filename": "test.png"
             }):
            tracemalloc.start()
            try:
                baseline, _ = tracemalloc.get_traced_memory()
                for i in range(20):
                    task_id = f"memory-{i}"
                    self.create_task(task_id, "image")
                    media_app.run_image_task(task_id, self.settings)
                    self.assertEqual(media_app.get_task_status(task_id)["status"], "completed")
                current, peak = tracemalloc.get_traced_memory()
                self.assertGreater(peak - baseline, 4_000_000)
                self.assertLess(current - baseline, 1_000_000)
            finally:
                tracemalloc.stop()

    def test_video_terminal_paths_evict_cache(self):
        for status in ("completed", "failed", "error"):
            with self.subTest(status=status):
                self.create_task(status, "video")
                with patch.object(media_app.video_runner, "build_payload", return_value={}), \
                     patch.object(media_app.video_runner, "create_task", return_value={}), \
                     patch.object(media_app.video_runner, "extract_task_id", return_value="remote"), \
                     patch.object(media_app.video_runner, "poll_task", return_value={"status": status},
                                  side_effect=RuntimeError("poll failed") if status == "error" else None), \
                     patch.object(media_app.video_runner, "resolve_media_url", return_value="https://test/video"), \
                     patch.object(media_app, "save_video_asset", return_value={"url": "/downloads/test.mp4", "filename": "test.mp4"}):
                    media_app.run_video_task(status, self.settings)
                self.assertEqual(media_app.tasks_store, {})
                self.assertEqual(media_app.get_task_status(status)["status"], status)

    def test_failed_commit_does_not_evict_task(self):
        self.create_task("pending", "image")
        with patch.object(media_app, "db_update_task", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                media_app.db_mark_finished("pending", media_app.tasks_store["pending"], "completed")
        self.assertIn("pending", media_app.tasks_store)

    def test_running_status_omits_diagnostics(self):
        self.create_task("running", "image")
        media_app.tasks_store["running"].update(raw={"large": "data"}, request_payload={"prompt": "test"})
        task = media_app.get_task_status("running")
        self.assertNotIn("raw", task)
        self.assertNotIn("request_payload", task)
        self.assertIn("request_payload", media_app.get_task_status("running", detail=True))
        self.assertEqual(media_app.get_task_status("missing").status_code, 404)

    def test_data_urls_are_summarized_without_mutating_input(self):
        payload = {"image_urls": ["data:image/png;base64,AAAA"], "prompt": "hello"}
        summary = media_app.summarize_task_data(payload)
        self.assertNotIn("base64,AAAA", json.dumps(summary))
        self.assertEqual(summary["prompt"], "hello")
        self.assertEqual(payload["image_urls"][0], "data:image/png;base64,AAAA")

    def test_database_connection_is_closed(self):
        with media_app.config_connection() as conn:
            conn.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_video_streams_to_disk_and_closes_response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"content-type": "video/mp4"}
        response.iter_content.return_value = iter([b"first", b"second"])
        with patch.object(media_app.requests, "get", return_value=response) as get, \
             patch.object(media_app, "save_video_thumbnail", return_value=None):
            asset = media_app.save_video_asset("https://test/video.mp4", self.settings)
        self.assertTrue(get.call_args.kwargs["stream"])
        self.assertEqual((media_app.DOWNLOAD_DIR / asset["filename"]).read_bytes(), b"firstsecond")
        response.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
