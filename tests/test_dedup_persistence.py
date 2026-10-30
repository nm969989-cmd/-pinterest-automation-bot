import datetime
from unittest.mock import patch

import jsonbin_sync
import scheduler


def _payload():
    return {
        "uploaded_files": [
            {"filename": "new.jpg", "title": "n", "anime_name": "a", "image_url": "u_new",
             "uploaded_at": "2026-10-30 10:00:00", "sha256": "", "phash": "", "is_organic": 0},
        ],
        "processed_posts": ["c/2"],
        "pin_queue": [
            {"post_id": "c_1", "image_path": "post_c_1.jpg", "image_url": "u_old", "sha256": "", "phash": ""},
            {"post_id": "c_9", "image_path": "post_c_9.jpg", "image_url": "u_fresh", "sha256": "", "phash": ""},
        ],
    }


def test_merge_keeps_cloud_history_and_drops_posted_queue_items():
    cloud = {
        "uploaded_files": [
            {"filename": "post_c_1.jpg", "image_url": "u_old", "uploaded_at": "2026-10-01 00:00:00",
             "sha256": "", "title": "", "anime_name": ""},
        ],
        "processed_posts": ["c/1"],
    }
    with patch.object(jsonbin_sync, "load_cloud_state", return_value=cloud):
        out = jsonbin_sync._merge_with_cloud(_payload())
    names = {u["filename"] for u in out["uploaded_files"]}
    assert names == {"new.jpg", "post_c_1.jpg"}
    assert set(out["processed_posts"]) == {"c/1", "c/2"}
    assert [q["post_id"] for q in out["pin_queue"]] == ["c_9"]


def test_merge_survives_cloud_failure():
    with patch.object(jsonbin_sync, "load_cloud_state", side_effect=RuntimeError("down")):
        out = jsonbin_sync._merge_with_cloud(_payload())
    assert len(out["pin_queue"]) == 2


def test_catchup_window_blocks_night():
    for hour, expected in [(1, False), (8, False), (9, True), (15, True), (20, True), (21, False), (23, False)]:
        fake = datetime.datetime(2026, 10, 30, hour, 0)
        with patch.object(scheduler, "_ist_now", return_value=fake):
            assert scheduler._in_catchup_window() is expected

