"""Real-time audio spectrum visualizer — captures system audio via parec."""
from __future__ import annotations

import subprocess
import threading

import numpy as np

BARS = 48
SAMPLE_RATE = 44100
CHUNK = 2048


def _find_monitor_source() -> str | None:
    """Return the name of the first PulseAudio/PipeWire monitor source."""
    try:
        out = subprocess.check_output(
            ["pactl", "list", "short", "sources"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        for line in out.splitlines():
            if ".monitor" in line:
                return line.split()[1]
    except Exception:
        pass
    return None


class AudioVisualizer:
    def __init__(self, bars: int = BARS) -> None:
        self._bars = bars
        self._levels: list[float] = [0.0] * bars
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._bin_edges = np.logspace(np.log10(60), np.log10(16000), bars + 1)

    def start(self) -> bool:
        """Start capturing audio. Returns False if parec is unavailable."""
        monitor = _find_monitor_source()
        cmd = [
            "parec",
            "--format=s16le",
            f"--rate={SAMPLE_RATE}",
            "--channels=1",
            "--latency-msec=30",
        ]
        if monitor:
            cmd += ["--device", monitor]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            return False
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        freqs = np.fft.rfftfreq(CHUNK, 1.0 / SAMPLE_RATE)
        window = np.hanning(CHUNK)
        peak = 1e-6

        while self._running and self._proc:
            raw = self._proc.stdout.read(CHUNK * 2)
            if len(raw) < CHUNK * 2:
                break
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            fft = np.abs(np.fft.rfft(samples * window))

            levels = []
            for i in range(self._bars):
                mask = (freqs >= self._bin_edges[i]) & (freqs < self._bin_edges[i + 1])
                levels.append(float(np.sqrt(np.mean(fft[mask] ** 2))) if mask.any() else 0.0)

            arr = np.array(levels)
            peak = max(peak * 0.998, float(arr.max()) or 1e-6)
            normalized = np.clip(arr / peak, 0.0, 1.0).tolist()

            with self._lock:
                alpha = 0.55
                self._levels = [
                    alpha * n + (1 - alpha) * o
                    for n, o in zip(normalized, self._levels)
                ]

    def get_levels(self) -> list[float]:
        with self._lock:
            return list(self._levels)

    def stop(self) -> None:
        self._running = False
        if self._proc:
            self._proc.terminate()
            self._proc = None
