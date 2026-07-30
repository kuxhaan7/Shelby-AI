"""Background task scheduler — cron jobs that run Shelby skills on a schedule."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger(__name__)
from .paths import tasks_file

_TASKS_FILE = tasks_file()
_DATA_DIR = _TASKS_FILE.parent

# Heartbeat interval in minutes
_HEARTBEAT_MINUTES = 30


class TaskScheduler:
    """Wrap APScheduler with persist/restore and a heartbeat job."""

    def __init__(self, skill_registry=None) -> None:
        self._sched = BackgroundScheduler(daemon=True)
        self._registry = skill_registry
        self._tasks: dict[str, dict[str, Any]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._sched.start()
        self._add_heartbeat()
        self._load_persisted()
        log.info("TaskScheduler started — heartbeat every %d min", _HEARTBEAT_MINUTES)

    def shutdown(self) -> None:
        self._sched.shutdown(wait=False)
        log.info("TaskScheduler stopped")

    # ── Public API ────────────────────────────────────────────────────────────

    def schedule(self, name: str, cron: str, skill_name: str, kwargs: dict | None = None) -> str:
        """Schedule *skill_name* to run on *cron* (standard 5-field expression)."""
        kwargs = kwargs or {}

        if name.startswith("__"):
            return "Names starting with __ are reserved."

        try:
            trigger = CronTrigger.from_crontab(cron)
        except Exception as exc:
            return f"Invalid cron expression '{cron}': {exc}"

        if name in self._tasks:
            try:
                self._sched.remove_job(name)
            except Exception:
                pass

        registry = self._registry  # capture for closure

        def _run() -> None:
            log.info("Running scheduled task '%s' → skill '%s'", name, skill_name)
            from .notify import notify_telegram
            try:
                result = registry.run(skill_name, kwargs) if registry else "No skill registry."
                log.info("Task '%s' result: %s", name, str(result)[:200])
                notify_telegram(f"Scheduled task '{name}' ran.\n\n{result}")
            except Exception as exc:
                log.error("Task '%s' failed: %s", name, exc)
                notify_telegram(f"Scheduled task '{name}' failed: {exc}")

        self._sched.add_job(_run, trigger=trigger, id=name, replace_existing=True)
        self._tasks[name] = {"cron": cron, "skill": skill_name, "kwargs": kwargs}
        self._persist()

        job = self._sched.get_job(name)
        next_run = str(job.next_run_time) if job else "unknown"
        return f"Task '{name}' scheduled ({cron} → {skill_name}). Next run: {next_run}"

    def cancel(self, name: str) -> str:
        """Remove a scheduled task by name."""
        if name not in self._tasks:
            return f"No task named '{name}'."
        try:
            self._sched.remove_job(name)
        except Exception:
            pass
        del self._tasks[name]
        self._persist()
        return f"Task '{name}' cancelled and removed."

    def list_tasks(self) -> list[dict[str, str]]:
        """Return all scheduled tasks with next-run times."""
        result = []
        for name, info in self._tasks.items():
            job = self._sched.get_job(name)
            result.append({
                "name": name,
                "cron": info["cron"],
                "skill": info["skill"],
                "kwargs": json.dumps(info.get("kwargs", {})),
                "next_run": str(job.next_run_time) if job else "not scheduled",
            })
        return result

    # ── Internals ─────────────────────────────────────────────────────────────

    def _add_heartbeat(self) -> None:
        def _beat() -> None:
            n = len(self._tasks)
            log.info("Shelby heartbeat — alive, %d user task(s) scheduled", n)
            from .notify import notify_telegram
            notify_telegram(
                f"Shelby heartbeat: alive, {n} scheduled task(s) running."
                if n else "Shelby heartbeat: alive."
            )

        self._sched.add_job(
            _beat,
            trigger="interval",
            minutes=_HEARTBEAT_MINUTES,
            id="__heartbeat__",
            replace_existing=True,
        )

    def _persist(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _TASKS_FILE.write_text(json.dumps(self._tasks, indent=2))

    def _load_persisted(self) -> None:
        if not _TASKS_FILE.exists():
            return
        try:
            tasks: dict = json.loads(_TASKS_FILE.read_text())
            for name, info in tasks.items():
                self.schedule(name, info["cron"], info["skill"], info.get("kwargs", {}))
            log.info("Restored %d scheduled task(s) from disk", len(tasks))
        except Exception as exc:
            log.error("Could not restore scheduled tasks: %s", exc)
