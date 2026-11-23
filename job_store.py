"""Shared async job manager and recursion-depth guard for Hermes <-> Antigravity bridge."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


DEFAULT_MAX_BRIDGE_DEPTH = 10
DEPTH_ENV_VAR = "HERMES_AGY_BRIDGE_DEPTH"


def get_bridge_depth() -> int:
    try:
        return int(os.environ.get(DEPTH_ENV_VAR, "0"))
    except ValueError:
        return 0


def check_recursion_guard(max_depth: Optional[int] = None) -> Optional[str]:
    limit = max_depth if max_depth is not None else int(
        os.environ.get("HERMES_AGY_MAX_DEPTH", str(DEFAULT_MAX_BRIDGE_DEPTH))
    )
    current = get_bridge_depth()
    if current >= limit:
        return (
            f"Bridge recursion limit reached ({DEPTH_ENV_VAR}={current} >= {limit}). "
            "Refusing nested cross-agent call to prevent infinite ping-pong."
        )
    return None


def next_hop_env(base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env[DEPTH_ENV_VAR] = str(get_bridge_depth() + 1)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@dataclass
class BridgeJob:
    job_id: str
    target: str
    prompt_preview: str
    cwd: str
    status: str = "running"  # running | completed | failed
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self, include_result: bool = True) -> Dict[str, Any]:
        now = time.time()
        elapsed = round((self.finished_at or now) - self.started_at, 2)
        data: Dict[str, Any] = {
            "job_id": self.job_id,
            "target": self.target,
            "status": self.status,
            "cwd": self.cwd,
            "prompt_preview": self.prompt_preview,
            "elapsed_seconds": elapsed,
        }
        if self.error:
            data["error"] = self.error
        if include_result and self.result is not None:
            data["result"] = self.result
        return data


class JobManager:
    """In-process thread-backed async job runner with JSON log persistence."""

    def __init__(self, target_name: str, state_dir: Optional[Path] = None) -> None:
        self.target_name = target_name
        self.state_dir = state_dir or (Path(__file__).resolve().parent / "state")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, BridgeJob] = {}

    def _persist(self, job: BridgeJob) -> None:
        try:
            path = self.state_dir / f"{self.target_name}_{job.job_id}.json"
            path.write_text(
                json.dumps(job.to_dict(include_result=True), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def start(
        self,
        prompt: str,
        cwd: str,
        fn: Callable[[], Dict[str, Any]],
    ) -> BridgeJob:
        job_id = uuid.uuid4().hex[:10]
        preview = prompt.strip().replace("\n", " ")[:140]
        job = BridgeJob(
            job_id=job_id,
            target=self.target_name,
            prompt_preview=preview,
            cwd=cwd,
        )
        with self._lock:
            self._jobs[job_id] = job
        self._persist(job)

        def _worker() -> None:
            try:
                res = fn()
                with self._lock:
                    job.result = res
                    job.status = "completed" if res.get("ok", False) else "failed"
                    if not res.get("ok", False):
                        job.error = str(res.get("error", "Unknown failure"))
                    job.finished_at = time.time()
            except Exception as exc:
                with self._lock:
                    job.status = "failed"
                    job.error = str(exc)
                    job.finished_at = time.time()
            finally:
                self._persist(job)

        t = threading.Thread(target=_worker, name=f"bridge-{self.target_name}-{job_id}", daemon=True)
        t.start()
        return job

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                return job.to_dict(include_result=True)
        # Fallback: check persisted disk record from earlier server run
        path = self.state_dir / f"{self.target_name}_{job_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def list_jobs(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)[:limit]
            return [j.to_dict(include_result=False) for j in jobs]
