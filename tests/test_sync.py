from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pick_next_film import pick_next  # noqa: E402
from reconcile_account import reconcile  # noqa: E402
from sdilej_upload import (  # noqa: E402
    SdilejTemporaryError,
    file_id_from_url,
    validate_upload_result,
)


class UploadResponseTests(unittest.TestCase):
    def test_rejects_zero_file_id(self) -> None:
        self.assertIsNone(file_id_from_url("https://sdilej.cz/0/movie.mp4"))

    def test_accepts_positive_file_id(self) -> None:
        self.assertEqual(123, file_id_from_url("https://sdilej.cz/123/movie.mp4"))

    def test_zero_file_id_is_temporary_service_error(self) -> None:
        with self.assertRaises(SdilejTemporaryError):
            validate_upload_result({"url": "https://sdilej.cz/0/movie.mp4"})


class PickerTests(unittest.TestCase):
    def test_uses_sledujteto_fallback_after_prehrajto_candidates_fail(self) -> None:
        film = {
            "cr_film_id": 10,
            "title": "Film",
            "year": 2026,
            "candidates": [{"upload_id": "dead", "url": "https://prehraj.to/dead"}],
            "sledujteto_source": {"file_id": 55, "cdn": "www"},
        }
        state = {
            "uploads": [],
            "in_progress": [],
            "failed_attempts": [{
                "cr_film_id": 10,
                "upload_id": "dead",
                "reason": "resolve_failed: 404",
                "permanent": True,
            }],
        }
        self.assertEqual(film, pick_next(state, [film]))


class ReconciliationTests(unittest.TestCase):
    def test_missing_and_zero_urls_return_to_retry_queue(self) -> None:
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        state = {
            "uploads": [
                {"cr_film_id": 1, "sdilej_url": "https://sdilej.cz/100/a.mp4", "uploaded_at": old},
                {"cr_film_id": 2, "sdilej_url": "https://sdilej.cz/0/b.mp4", "uploaded_at": old},
            ],
            "failed_attempts": [{"cr_film_id": 2, "reason": "upload_failed: old"}],
            "in_progress": [],
        }
        backlog = [
            {"cr_film_id": 1, "title": "A", "year": 2026, "display_name": "A"},
            {"cr_film_id": 2, "title": "B", "year": 2026, "display_name": "B"},
        ]
        stats = reconcile(state, backlog, {100: {"url": "https://sdilej.cz/100/a.mp4", "name": "A.mp4"}})
        self.assertEqual([1], [item["cr_film_id"] for item in state["uploads"]])
        self.assertEqual(1, stats["missing_uploads"])
        self.assertEqual([], state["failed_attempts"])


if __name__ == "__main__":
    unittest.main()
