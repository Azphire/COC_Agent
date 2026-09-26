"""Append-only phase timings, separate from game events and their public projection."""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_context = ContextVar("runtime_timing_context", default={})
logger = logging.getLogger(__name__)


class RuntimeTimings:
    def __init__(self, path):
        self.path = path
        self.write_failed = False

    def context(self, **values):
        """Set IDs for the current runtime task; no state crosses asyncio tasks."""
        _context.set({**_context.get(), **values})

    def _write(self, row):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            # Instrumentation cannot fail a world transaction or trigger a retry.
            if not self.write_failed:
                logger.warning("Runtime phase timing persistence unavailable")
            self.write_failed = True

    def point(self, phase, **values):
        self._write({
            "timing_version": 1, "room_id": None, "cycle_id": None, "run_id": None,
            "callback": None, **_context.get(), **values, "phase": phase,
            "status": "observed", "at": time.time(), "elapsed_ms": None,
        })

    @contextmanager
    def scope(self, **values):
        token = _context.set({**_context.get(), **values})
        try:
            yield
        finally:
            _context.reset(token)

    @contextmanager
    def span(self, phase, **values):
        context = {**_context.get(), **values}
        started = time.monotonic()
        row = {
            "timing_version": 1, "room_id": None, "cycle_id": None, "run_id": None,
            "callback": None, **context, "phase": phase, "span_id": str(uuid4()),
            "parent_span_id": context.get("span_id"), "started_at": time.time(),
            "finished_at": None, "elapsed_ms": None, "status": "started",
        }
        self._write(row)
        token = _context.set({**context, "span_id": row["span_id"]})
        try:
            yield row
        except BaseException as error:
            row.update(status="failed", error_type=type(error).__name__)
            raise
        else:
            row["status"] = "completed"
        finally:
            row.update(finished_at=time.time(),
                       elapsed_ms=round((time.monotonic() - started) * 1000, 3))
            self._write(row)
            _context.reset(token)


def stage_state(previous, status, *, now=None, **fields):
    """Keep timestamps for failed/finished stages; absent starts stay unknown."""
    now = time.time() if now is None else now
    prior = dict(previous or {})
    if status == "running":
        return {**prior, **fields, "status": status, "started_at": now,
                "finished_at": None, "elapsed_ms": None}
    started = prior.get("started_at")
    return {**prior, **fields, "status": status, "finished_at": now,
            "elapsed_ms": round(max(0, now - started) * 1000, 3)
            if isinstance(started, (int, float)) else None}
