"""Background trace worker with debounce + a settings-snapshot undo stack."""
from __future__ import annotations

import traceback

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from lasertrace.models import Job
from lasertrace.pipeline import PipelineOutput, run


class TraceWorker(QObject):
    finished = Signal(object)   # PipelineOutput
    failed = Signal(str)

    @Slot(object, object)
    def trace(self, rgb: np.ndarray, job_json: str) -> None:
        try:
            job = Job.model_validate_json(job_json)
            out: PipelineOutput = run(rgb, job)
            self.finished.emit(out)
        except Exception:
            self.failed.emit(traceback.format_exc())


class TraceRunner(QObject):
    """Owns the worker thread; coalesces rapid slider changes into one run."""
    request = Signal(object, object)
    result = Signal(object)
    error = Signal(str)
    busy = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread = QThread()
        self._worker = TraceWorker()
        self._worker.moveToThread(self._thread)
        self.request.connect(self._worker.trace)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._thread.start()
        self._running = False
        self._pending: tuple | None = None

    def submit(self, rgb: np.ndarray, job: Job) -> None:
        payload = (rgb, job.model_dump_json())
        if self._running:
            self._pending = payload
            return
        self._running = True
        self.busy.emit(True)
        self.request.emit(*payload)

    def _on_done(self, out) -> None:
        self.result.emit(out)
        self._next()

    def _on_fail(self, msg: str) -> None:
        self.error.emit(msg)
        self._next()

    def _next(self) -> None:
        if self._pending is not None:
            payload, self._pending = self._pending, None
            self.request.emit(*payload)
        else:
            self._running = False
            self.busy.emit(False)

    def shutdown(self) -> None:
        self._thread.quit()
        self._thread.wait(2000)


class History:
    """Undo/redo over job JSON snapshots."""

    def __init__(self, limit: int = 100):
        self._undo: list[str] = []
        self._redo: list[str] = []
        self.limit = limit

    def push(self, job: Job) -> None:
        s = job.model_dump_json()
        if self._undo and self._undo[-1] == s:
            return
        self._undo.append(s)
        del self._undo[: -self.limit]
        self._redo.clear()

    def undo(self, current: Job) -> Job | None:
        if len(self._undo) < 2:
            return None
        self._redo.append(self._undo.pop())
        return Job.model_validate_json(self._undo[-1])

    def redo(self) -> Job | None:
        if not self._redo:
            return None
        s = self._redo.pop()
        self._undo.append(s)
        return Job.model_validate_json(s)
