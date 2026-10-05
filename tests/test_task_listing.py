import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as media_app


class TaskListingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = Path(directory.name) / "config.db"
        patcher = patch.object(media_app, "CONFIG_DB_PATH", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        # sqlite3's context manager commits/rolls back but does not close.
        # Close all test connections before removing the temporary DB on Windows.
        connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)
            self.addCleanup(conn.close)
            return conn

        connections = patch.object(media_app.sqlite3, "connect", side_effect=tracked_connect)
        connections.start()
        self.addCleanup(connections.stop)
        media_app.init_config_db()
        with sqlite3.connect(self.db) as conn:
            for i, task_type in enumerate(("image", "video", "image")):
                conn.execute(
                    "INSERT INTO media_tasks "
                    "(id, type, status, model, prompt, created_at, local_url, "
                    "thumbnail_url, raw, request_payload, logs) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(i), task_type, "completed", "test-model", "prompt",
                        f"2026-09-24 12:00:0{i}", f"/downloads/{i}.png",
                        f"/downloads/thumbs/{i}.jpg",
                        json.dumps({"data": "x" * 100_000}),
                        json.dumps({"prompt": "detail"}), json.dumps(["done"]),
                    ),
                )

    def test_list_never_reads_detail_columns_and_preserves_pagination(self):
        connect = sqlite3.connect

        def guarded_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)

            def authorize(action, table, column, database, source):
                if action == sqlite3.SQLITE_READ and table == "media_tasks":
                    if column in {"raw", "request_payload", "logs"}:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            return conn

        with patch.object(media_app.sqlite3, "connect", side_effect=guarded_connect):
            first = media_app.db_list_tasks(page_size=2)
            second = media_app.db_list_tasks(page=2, page_size=2)
            images = media_app.db_list_tasks(page_size=1, task_type="image")
            videos = media_app.db_list_tasks(task_type="video")

        self.assertEqual([t["id"] for t in first["tasks"]], ["2", "1"])
        self.assertEqual([t["id"] for t in second["tasks"]], ["0"])
        self.assertEqual((first["total"], first["total_pages"]), (3, 2))
        self.assertEqual((images["total"], images["total_pages"]), (2, 2))
        self.assertEqual(images["tasks"][0]["id"], "2")
        self.assertEqual([t["id"] for t in videos["tasks"]], ["1"])
        task = first["tasks"][0]
        self.assertEqual(task["thumbnailUrl"], "/downloads/thumbs/2.jpg")
        self.assertEqual(task["asset"]["url"], "/downloads/2.png")
        self.assertEqual(task["logs"], [])
        self.assertNotIn("raw", task)
        self.assertNotIn("request_payload", task)

    def test_detail_still_includes_raw_payload_and_logs(self):
        task = media_app.db_get_task("2")
        self.assertEqual(task["raw"], media_app.summarize_task_data({"data": "x" * 100_000}))
        self.assertEqual(task["request_payload"], {"prompt": "detail"})
        self.assertEqual(task["logs"], ["done"])

    def test_status_never_reads_legacy_media_columns(self):
        connect = sqlite3.connect

        def guarded_connect(*args, **kwargs):
            conn = connect(*args, **kwargs)

            def authorize(action, table, column, database, source):
                if action == sqlite3.SQLITE_READ and table == "media_tasks":
                    if column in {"raw", "request_payload"}:
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            conn.set_authorizer(authorize)
            return conn

        with patch.object(media_app.sqlite3, "connect", side_effect=guarded_connect):
            task = media_app.get_task_status("2")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["logs"], ["done"])
        self.assertEqual(task["image_url"], "/downloads/2.png")
        self.assertNotIn("raw", task)
        self.assertNotIn("request_payload", task)

    def test_startup_adds_indexes_to_existing_database_idempotently(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP INDEX idx_media_tasks_created_at")
            conn.execute("DROP INDEX idx_media_tasks_type_created_at")

        media_app.init_config_db()
        media_app.init_config_db()

        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM media_tasks").fetchone()[0], 3)
            for where, expected in (
                ("", "idx_media_tasks_created_at"),
                ("WHERE type = 'image'", "idx_media_tasks_type_created_at"),
            ):
                plan = conn.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM media_tasks "
                    f"{where} ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                description = " ".join(row[3] for row in plan)
                self.assertIn(expected, description)
                self.assertNotIn("USE TEMP B-TREE", description)


if __name__ == "__main__":
    unittest.main()
