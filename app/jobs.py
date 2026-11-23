"""Background jobs in three lanes (interactive, background, lists) plus the scheduler."""
from __future__ import annotations

import asyncio
import contextvars
import logging
import time
import traceback
from typing import Any, Awaitable, Callable

from . import config, db, settings
from .util import event

log = logging.getLogger("programarr.jobs")

_current_job: contextvars.ContextVar[int | None] = contextvars.ContextVar("programarr_job", default=None)


class Cancelled(Exception):
    pass


class Runner:
    def __init__(self) -> None:
        self.queues: dict[str, asyncio.Queue] = {}
        self.workers: list[asyncio.Task] = []
        self.cancel_requested: set[int] = set()
        self.tasks: set[asyncio.Task] = set()
        self.scheduler_task: asyncio.Task | None = None
        self.started = False

    # -- lifecycle -------------------------------------------------------
    def start(self, scheduler: bool = True) -> None:
        if self.started:
            return
        self.started = True
        database = db.get()
        database.execute(
            "UPDATE jobs SET status='failed', message='Stopped by a restart', finished_at=? "
            "WHERE status IN ('queued','running')",
            (db.now(),),
        )
        for lane in ("ui", "bg", "flows"):
            self.queues[lane] = asyncio.Queue()
            self.workers.append(asyncio.create_task(self._worker(lane)))
        if scheduler:
            self.scheduler_task = asyncio.create_task(self._scheduler())

    async def stop(self) -> None:
        for task in [*self.workers, *self.tasks, *([self.scheduler_task] if self.scheduler_task else [])]:
            task.cancel()
        for task in [*self.workers, *self.tasks]:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self.workers.clear()
        self.started = False

    # -- submitting --------------------------------------------------------
    def submit(self, kind: str, title: str, func: Callable[..., Awaitable[Any]], *args, lane: str = "bg",
               dedupe: bool = False, **kwargs) -> int:
        database = db.get()
        if dedupe:
            existing = database.one(
                "SELECT id FROM jobs WHERE kind=? AND status IN ('queued','running') ORDER BY id LIMIT 1", (kind,)
            )
            if existing:
                return existing["id"]
        cursor = database.execute(
            "INSERT INTO jobs (kind, title, status, created_at) VALUES (?,?, 'queued', ?)", (kind, title, db.now())
        )
        job_id = cursor.lastrowid
        if not self.started:
            raise RuntimeError("Jobs are not running yet")
        self.queues[lane].put_nowait((job_id, func, args, kwargs))
        return job_id

    def later(self, seconds: float, func: Callable[..., Awaitable[Any]], *args) -> None:
        async def run():
            await asyncio.sleep(seconds)
            try:
                await func(*args)
            except Exception as exc:  # noqa: BLE001
                log.warning("Follow-up failed: %s", exc)

        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def cancel(self, job_id: int) -> None:
        self.cancel_requested.add(job_id)
        db.get().execute(
            "UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'", (db.now(), job_id)
        )

    # -- worker --------------------------------------------------------------
    async def _worker(self, lane: str) -> None:
        queue = self.queues[lane]
        database = db.get()
        while True:
            job_id, func, args, kwargs = await queue.get()
            row = database.one("SELECT status FROM jobs WHERE id=?", (job_id,))
            if not row or row["status"] == "cancelled":
                queue.task_done()
                continue
            database.execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (db.now(), job_id))
            token = _current_job.set(job_id)
            try:
                result = await func(*args, **kwargs)
                status = "cancelled" if job_id in self.cancel_requested else "done"
                database.execute(
                    "UPDATE jobs SET status=?, result=?, finished_at=? WHERE id=?",
                    (status, db.dumps(result) if result is not None else None, db.now(), job_id),
                )
            except Cancelled:
                database.execute(
                    "UPDATE jobs SET status='cancelled', message='Cancelled', finished_at=? WHERE id=?",
                    (db.now(), job_id),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.error("Job %s failed: %s\n%s", job_id, exc, traceback.format_exc())
                database.execute(
                    "UPDATE jobs SET status='failed', message=?, finished_at=? WHERE id=?",
                    (str(exc)[:500], db.now(), job_id),
                )
                event("error", "job", f"Job failed: {exc}")
            finally:
                _current_job.reset(token)
                self.cancel_requested.discard(job_id)
                queue.task_done()

    async def drain(self, timeout: float = 30.0) -> None:
        """Wait until all queued jobs are done (tests and the demo)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = db.get().scalar("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')", (), 0)
            if not busy and not self.tasks:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("Jobs are still running")

    # -- scheduler -------------------------------------------------------------
    async def _scheduler(self) -> None:
        from .posters import tasks  # late import: tasks depends on most modules

        await asyncio.sleep(5)
        last_maintenance = 0.0
        while True:
            try:
                now = time.time()
                for kind in due(now):
                    if kind == "sync":
                        self.submit("sync", "Sync with Plex", tasks.sync_and_process, dedupe=True)
                    elif kind == "setcheck":
                        self.submit("setcheck", "Check linked sets", tasks.check_sets, dedupe=True)
                if now - last_maintenance >= 6 * 3600:
                    last_maintenance = now
                    self.submit("maintenance", "Clean up", tasks.maintenance, dedupe=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Scheduler: %s", exc)
            await asyncio.sleep(30)


runner = Runner()


def due(now: float) -> list[str]:
    """Which regular jobs are due (Plex sync, set check)."""
    if not (settings.get("setup_done") and settings.get("plex_url")):
        return []
    result = []
    poll = max(5, int(settings.get("poll_minutes") or 15)) * 60
    if now - (settings.get("last_sync") or 0) >= poll:
        result.append("sync")
    check = max(1, int(settings.get("set_check_hours") or 24)) * 3600
    # Set checks write posters for waiting seasons: only with automation on.
    if settings.get("automation") and now - (settings.get("last_set_check") or 0) >= check:
        result.append("setcheck")
    return result


def progress(done: int | None = None, total: int | None = None, message: str | None = None) -> None:
    job_id = _current_job.get()
    if job_id is None:
        return
    values: dict = {}
    if done is not None:
        values["done"] = done
    if total is not None:
        values["total"] = total
    if message is not None:
        values["message"] = message[:300]
    db.get().update("jobs", "id", job_id, values)
    if job_id in runner.cancel_requested:
        raise Cancelled()


def current_job() -> int | None:
    return _current_job.get()


def scheduler_enabled() -> bool:
    return config.SCHEDULER
