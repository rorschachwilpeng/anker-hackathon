from datetime import datetime
from zoneinfo import ZoneInfo


def test_next_run_rolls_to_tomorrow():
    from app.knowledge.worker import next_run

    now = datetime(2026, 9, 20, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_run(now).isoformat() == "2026-09-21T02:00:00+08:00"


def test_next_run_today_before_two():
    from app.knowledge.worker import next_run

    now = datetime(2026, 9, 20, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert next_run(now).isoformat() == "2026-09-20T02:00:00+08:00"


def test_missed_daily_run_is_caught_up_once():
    from app.knowledge.worker import due_run_id

    zone = ZoneInfo("Asia/Shanghai")
    now = datetime(2026, 9, 20, 9, 0, tzinfo=zone)
    assert due_run_id(now, last_completed_date="2026-09-19") == "mining-2026-09-20"
    assert due_run_id(now, last_completed_date="2026-09-20") is None

