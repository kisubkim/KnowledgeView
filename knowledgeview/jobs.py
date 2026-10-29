"""In-memory generation queue. One worker thread runs jobs in order; jobs are lost on restart."""

from __future__ import annotations

import queue
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from .llm import Cancelled

MAX_KEPT = 300


class Job:
    def __init__(self, *, source_path: Path, source_root: Path, source_label: str, category: str, mode: str,
                 refresh: bool = False, output_rel: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.source_path = source_path
        self.source_root = source_root
        self.source_label = source_label
        self.category = category
        self.mode = mode
        self.refresh = refresh  # ignore cached LLM results
        self.output_rel = output_rel
        self.status = "queued"  # queued | running | done | failed | cancelled
        self.done = 0
        self.total = 0
        self.message = "대기 중"
        self.log: list[str] = []
        self.result: dict | None = None
        self.error = ""
        self.created_at = datetime.now().isoformat(timespec="seconds")
        self.started_at = ""
        self.finished_at = ""
        self.cancel = threading.Event()

    def add_log(self, msg: str) -> None:
        self.log.append(f"{datetime.now():%H:%M:%S} {msg}")
        del self.log[:-200]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "source": self.source_label, "category": self.category,
            "mode": self.mode, "refresh": self.refresh, "done": self.done, "total": self.total, "message": self.message,
            "log": self.log, "result": self.result, "error": self.error, "created_at": self.created_at,
            "started_at": self.started_at, "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, runner: Callable[[Job], dict]):
        self._runner = runner
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name="kv-jobs").start()

    def submit(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            finished = [j for j in self._jobs.values() if j.status in ("done", "failed", "cancelled")]
            for j in finished[: max(0, len(self._jobs) - MAX_KEPT)]:
                self._jobs.pop(j.id, None)
        job.add_log(f"등록: {job.source_label} → {job.category or '(최상위)'} [{job.mode}]")
        self._queue.put(job)
        return job

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j.to_dict() for j in reversed(jobs)]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job.status not in ("queued", "running"):
            return False
        job.cancel.set()
        if job.status == "queued":
            job.status = "cancelled"
            job.message = "취소됨"
        return True

    def clear_finished(self) -> None:
        with self._lock:
            for jid in [j.id for j in self._jobs.values() if j.status in ("done", "failed", "cancelled")]:
                self._jobs.pop(jid)

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job.cancel.is_set():
                continue
            job.status = "running"
            job.started_at = datetime.now().isoformat(timespec="seconds")
            job.add_log("시작")
            try:
                job.result = self._runner(job)
                job.status = "done"
                job.message = "완료" + (f" (경고 {len(job.result.get('warnings', []))}건)" if job.result.get("warnings") else "")
            except Cancelled:
                job.status = "cancelled"
                job.message = "취소됨"
            except Exception as e:  # noqa: BLE001 - report any failure on the job
                job.status = "failed"
                job.error = str(e) or e.__class__.__name__
                job.message = "실패: " + job.error
                job.add_log(traceback.format_exc(limit=3))
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.add_log(job.message)
