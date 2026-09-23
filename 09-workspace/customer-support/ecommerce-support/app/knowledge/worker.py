import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_run(now, zone="Asia/Shanghai"):
    timezone = ZoneInfo(zone)
    local = now.astimezone(timezone)
    target = datetime.combine(local.date(), time(2), timezone)
    if local >= target:
        target += timedelta(days=1)
    return target


def due_run_id(now, last_completed_date=None, zone="Asia/Shanghai"):
    local = now.astimezone(ZoneInfo(zone))
    if local.time() < time(2) or last_completed_date == local.date().isoformat():
        return None
    return "mining-" + local.date().isoformat()


async def run_worker(run_once, state, *, enabled=False, zone="Asia/Shanghai"):
    if not enabled:
        return {"status": "disabled"}
    timezone = ZoneInfo(zone)
    while True:
        now = datetime.now(timezone)
        last = await state.get("worker:last_completed") or {}
        run_id = due_run_id(now, last.get("date"), zone)
        if run_id:
            try:
                result = await run_once(run_id)
            except Exception:
                raise
            await state.put("worker:last_completed", {
                "date": now.date().isoformat(), "run_id": run_id,
                "status": "skipped" if isinstance(result, dict) and result.get("status") == "skipped" else "completed",
            })
            continue
        delay = max(1.0, (next_run(now, zone) - now).total_seconds())
        await asyncio.sleep(delay)
