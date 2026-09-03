"""External resource measurement of the candidate process tree via psutil.

External on purpose: a candidate's self-reported CPU/RSS is exactly the number you must
not trust. Sampling is on a background thread and never feeds a deterministic metric.
"""

from __future__ import annotations

import threading

from leanbench.schemas.events import ResourceSample

try:  # pragma: no cover - import guard
    import psutil
except ImportError as _exc:  # pragma: no cover
    psutil = None  # type: ignore[assignment]
    _PSUTIL_ERROR = str(_exc)
else:
    _PSUTIL_ERROR = ""


class ResourceMonitor:
    """Peak-RSS / CPU / IO sampler for a pid and its children."""

    def __init__(self, pid: int | None, *, interval_s: float) -> None:
        self.pid = pid
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._sample = ResourceSample(
            pid=pid,
            available=psutil is not None and pid is not None,
            reason=None if psutil is not None else f"psutil unavailable: {_PSUTIL_ERROR}",
        )

    def start(self) -> None:
        if not self._sample.available:
            return
        self._thread = threading.Thread(target=self._loop, name="lb-resources", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.sample_once()

    def sample_once(self) -> None:
        if psutil is None or self.pid is None:
            return
        try:
            proc = psutil.Process(self.pid)
            procs = [proc, *proc.children(recursive=True)]
            cpu_user = 0.0
            cpu_system = 0.0
            rss = 0
            io_read = 0
            io_write = 0
            for p in procs:
                try:
                    times = p.cpu_times()
                    cpu_user += times.user
                    cpu_system += times.system
                    rss += p.memory_info().rss
                    counters = getattr(p, "io_counters", None)
                    if counters is not None:
                        io = counters()
                        io_read += getattr(io, "read_bytes", 0)
                        io_write += getattr(io, "write_bytes", 0)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return
        except OSError:
            return
        with self._lock:
            prev = self._sample
            self._sample = ResourceSample(
                pid=self.pid,
                cpu_user_s=max(prev.cpu_user_s, cpu_user),
                cpu_system_s=max(prev.cpu_system_s, cpu_system),
                rss_peak_bytes=max(prev.rss_peak_bytes, rss),
                io_read_bytes=max(prev.io_read_bytes, io_read),
                io_write_bytes=max(prev.io_write_bytes, io_write),
                available=True,
            )

    def stop(self) -> ResourceSample:
        self.sample_once()
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_s * 4)
        with self._lock:
            return self._sample

    def snapshot(self) -> ResourceSample:
        with self._lock:
            return self._sample
