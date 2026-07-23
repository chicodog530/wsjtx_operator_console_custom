from __future__ import annotations

import queue
import struct
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

try:
    import numpy as np
    import sounddevice as sd
except Exception as exc:
    np = None
    sd = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = ""


BINARY_MAGIC = b"WF17"
BINARY_HEADER = struct.Struct("<4sIffHH")
BINARY_PEAK = struct.Struct("<HHh")
# magic, sequence, level_dbfs, suggested_floor_dbfs, bin_count, flags


@dataclass(slots=True)
class AudioStatus:
    available: bool = not bool(IMPORT_ERROR)
    running: bool = False
    recovering: bool = False
    device: int | None = None
    device_name: str = ""
    sample_rate: int = 48000
    fft_size: int = 4096
    fps: int = 15
    min_hz: int = 0
    max_hz: int = 5000
    error: str = IMPORT_ERROR
    sequence: int = 0
    level_db: float = -120.0
    dropped_blocks: int = 0
    rejected_frames: int = 0
    restarts: int = 0
    last_frame_at: float | None = None

    def public(self) -> dict[str, Any]:
        return asdict(self)


class AudioWaterfall:
    """Reliable shared-mode audio capture and calibrated FFT producer."""

    def __init__(self) -> None:
        self.status = AudioStatus()
        self._stream = None
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=24)
        self._thread: threading.Thread | None = None
        self._recovery_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._latest_binary: bytes | None = None
        self._desired_device: int | None = None
        self._desired_name = ""
        self._desired_fft = 4096
        self._desired_fps = 15
        self._manual_stop = True
        self._previous_median: float | None = None
        self._previous_level: float | None = None

    def devices(self) -> list[dict[str, Any]]:
        if sd is None:
            return []
        result = []
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels", 0)) > 0:
                result.append({
                    "id": i,
                    "name": str(d.get("name", "")),
                    "channels": int(d.get("max_input_channels", 0)),
                    "default_sample_rate": int(round(float(d.get("default_samplerate", 48000)))),
                    "hostapi": int(d.get("hostapi", 0)),
                })
        return result

    def _resolve_device(self, device: int | None, device_name: str = "") -> tuple[int, dict]:
        devices = sd.query_devices()
        # Prefer the persistent name because Windows may renumber USB devices.
        if device_name:
            wanted = device_name.casefold()
            exact = [
                (i, d) for i, d in enumerate(devices)
                if int(d.get("max_input_channels", 0)) > 0
                and str(d.get("name", "")).casefold() == wanted
            ]
            if exact:
                return exact[0]
            partial = [
                (i, d) for i, d in enumerate(devices)
                if int(d.get("max_input_channels", 0)) > 0
                and wanted in str(d.get("name", "")).casefold()
            ]
            if partial:
                return partial[0]

        if device is not None and 0 <= int(device) < len(devices):
            d = devices[int(device)]
            if int(d.get("max_input_channels", 0)) > 0:
                return int(device), d

        default = int(sd.default.device[0])
        if default >= 0:
            return default, devices[default]
        raise RuntimeError("No usable audio input device was found")

    def start(
        self,
        device: int | None,
        sample_rate: int = 48000,
        fft_size: int = 4096,
        fps: int = 15,
        device_name: str = "",
    ) -> tuple[bool, str]:
        if sd is None or np is None:
            return False, f"Audio support is unavailable: {IMPORT_ERROR}"

        self.stop()
        self._manual_stop = False
        self._stop.clear()
        self._desired_device = device
        self._desired_name = device_name
        self._desired_fft = max(1024, min(int(fft_size), 16384))
        self._desired_fps = max(5, min(int(fps), 30))

        ok, message = self._open_stream()
        if not ok:
            # Keep retrying in case the radio is temporarily disconnected.
            self.status.recovering = True

        self._ensure_recovery_thread()
        return ok, message

    def _open_stream(self) -> tuple[bool, str]:
        try:
            selected, info = self._resolve_device(self._desired_device, self._desired_name)
            native_rate = int(round(float(info.get("default_samplerate", 48000))))
            native_rate = max(8000, min(native_rate, 192000))

            self.status.device = selected
            self.status.device_name = str(info.get("name", ""))
            self.status.sample_rate = native_rate
            self.status.fft_size = self._desired_fft
            self.status.fps = self._desired_fps
            self.status.error = ""
            self.status.recovering = False
            self._desired_name = self.status.device_name

            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break

            block = max(256, int(native_rate / self.status.fps))
            self._stream = sd.InputStream(
                device=selected,
                channels=1,
                samplerate=native_rate,
                blocksize=block,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
            self.status.running = True
            self.status.restarts += 1

            self._thread = threading.Thread(
                target=self._worker,
                name="waterfall-fft",
                daemon=True,
            )
            self._thread.start()
            return True, (
                f"Capturing {self.status.device_name} at its native "
                f"{self.status.sample_rate} Hz rate"
            )
        except Exception as exc:
            self.status.running = False
            self.status.recovering = not self._manual_stop
            self.status.error = str(exc)
            return False, f"Could not open audio input: {exc}"

    def _ensure_recovery_thread(self) -> None:
        if self._recovery_thread and self._recovery_thread.is_alive():
            return
        self._recovery_thread = threading.Thread(
            target=self._recovery_loop,
            name="waterfall-recovery",
            daemon=True,
        )
        self._recovery_thread.start()

    def _recovery_loop(self) -> None:
        while not self._stop.wait(2.0):
            if self._manual_stop:
                return
            stream_ok = False
            try:
                stream_ok = bool(self._stream is not None and self._stream.active)
            except Exception:
                stream_ok = False

            stale = (
                self.status.last_frame_at is not None
                and time.time() - self.status.last_frame_at > 4.0
            )
            if self.status.running and stream_ok and not stale:
                continue

            self.status.running = False
            self.status.recovering = True
            self._close_stream()
            self._open_stream()

    def stop(self) -> None:
        self._manual_stop = True
        self._stop.set()
        self._close_stream()
        self.status.running = False
        self.status.recovering = False

    def _close_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _callback(self, indata, frames, time_info, callback_status) -> None:
        if callback_status:
            self.status.error = str(callback_status)
            self.status.dropped_blocks += 1
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.status.dropped_blocks += 1
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(indata[:, 0].copy())
            except queue.Empty:
                pass

    def _detect_peaks(
        self,
        values: Any,
        hz: Any,
        noise_db: float,
    ) -> list[tuple[int, int, int]]:
        """Return center Hz, width in tenths of Hz, and strength in centi-dB.

        These are deliberately conservative activity tracks. They help the
        browser stabilize labels before WSJT-X finishes decoding without
        pretending to be an FT8 decoder.
        """
        if len(values) < 3:
            return []
        threshold = noise_db + 7.0
        active = values >= threshold
        peaks: list[tuple[int, int, int]] = []
        i = 0
        while i < len(values):
            if not active[i]:
                i += 1
                continue
            j = i + 1
            while j < len(values) and active[j]:
                j += 1
            if j - i >= 2:
                section = values[i:j]
                weights = np.maximum(section - threshold, 0.1)
                center = float(np.sum(hz[i:j] * weights) / np.sum(weights))
                bin_width = float(hz[1] - hz[0]) if len(hz) > 1 else 1.0
                width = max(bin_width, float(hz[j - 1] - hz[i] + bin_width))
                strength = float(section.max())
                if 60 <= center <= self.status.max_hz and width <= 220:
                    peaks.append((
                        int(round(center)),
                        int(round(width * 10.0)),
                        int(round(strength * 100.0)),
                    ))
            i = j
        peaks.sort(key=lambda p: p[2], reverse=True)
        return peaks[:48]

    def _worker(self) -> None:
        fft_size = self.status.fft_size
        hop_size = max(128, fft_size // 4)  # 75% overlap
        window = np.hanning(fft_size).astype(np.float32)
        coherent_gain = max(float(np.sum(window)), 1.0)
        local_stream = self._stream
        sample_buffer = np.empty(0, dtype=np.float32)
        power_accumulator = None
        accumulated = 0
        last_publish = time.monotonic()
        publish_period = 1.0 / max(1, self.status.fps)

        hz_all = np.fft.rfftfreq(fft_size, 1 / self.status.sample_rate)
        mask = (hz_all >= self.status.min_hz) & (hz_all <= self.status.max_hz)
        hz = hz_all[mask]

        while (
            not self._stop.is_set()
            and not self._manual_stop
            and local_stream is self._stream
        ):
            try:
                block = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if len(sample_buffer):
                sample_buffer = np.concatenate((sample_buffer, block))
            else:
                sample_buffer = np.asarray(block, dtype=np.float32)

            # Process every overlapping window. Average linear power between
            # browser publications so the display is smooth without smearing.
            while len(sample_buffer) >= fft_size:
                frame = sample_buffer[:fft_size]
                sample_buffer = sample_buffer[hop_size:]

                centered = frame - float(np.mean(frame))
                rms = float(np.sqrt(np.mean(centered * centered) + 1e-20))
                level_db = float(20 * np.log10(rms + 1e-10))

                spectrum = np.fft.rfft(centered * window)
                amplitude = (2.0 * np.abs(spectrum)) / coherent_gain
                amplitude[0] *= 0.5
                linear_power = np.square(np.maximum(amplitude[mask], 1e-10))

                if power_accumulator is None:
                    power_accumulator = linear_power
                else:
                    power_accumulator += linear_power
                accumulated += 1

                now = time.monotonic()
                if now - last_publish < publish_period:
                    continue

                averaged_power = power_accumulator / max(1, accumulated)
                values = 10 * np.log10(np.maximum(averaged_power, 1e-20))
                power_accumulator = None
                accumulated = 0
                last_publish = now

                if len(values) >= 3:
                    smoothed = np.convolve(
                        values,
                        np.array([0.16, 0.68, 0.16], dtype=np.float64),
                        mode="same",
                    )
                    values = np.maximum(smoothed, values - 1.2)

                median = float(np.percentile(values, 25))
                suggested_floor = median - 8.0

                if (
                    self._previous_median is not None
                    and self._previous_level is not None
                    and abs(median - self._previous_median) > 28.0
                    and abs(level_db - self._previous_level) > 24.0
                ):
                    self.status.rejected_frames += 1
                    self._previous_median = median
                    self._previous_level = level_db
                    continue
                self._previous_median = median
                self._previous_level = level_db

                peaks = self._detect_peaks(values, hz, median)

                target = 640
                if len(values) > target:
                    edges = np.linspace(0, len(values), target + 1, dtype=int)
                    reduced = []
                    for i in range(target):
                        section = values[edges[i]:max(edges[i] + 1, edges[i + 1])]
                        reduced.append(float(section.max() * 0.78 + section.mean() * 0.22))
                    values = np.asarray(reduced, dtype=np.float64)

                sequence = self.status.sequence + 1
                quantized = np.clip(
                    np.rint(values * 100.0), -32768, 32767
                ).astype("<i2")
                flags = 1 if self.status.recovering else 0
                if peaks:
                    flags |= 2

                peak_bytes = struct.pack("<H", len(peaks))
                if peaks:
                    peak_bytes += b"".join(BINARY_PEAK.pack(*peak) for peak in peaks)

                binary = BINARY_HEADER.pack(
                    BINARY_MAGIC,
                    sequence,
                    level_db,
                    suggested_floor,
                    len(quantized),
                    flags,
                ) + quantized.tobytes() + peak_bytes

                payload = {
                    "sequence": sequence,
                    "values_dbfs": [round(float(v), 2) for v in values],
                    "min_hz": self.status.min_hz,
                    "max_hz": self.status.max_hz,
                    "level_db": round(level_db, 1),
                    "suggested_floor": round(suggested_floor, 1),
                    "peaks": [
                        {
                            "hz": center,
                            "width_hz": width_tenths / 10.0,
                            "strength_db": strength_centi / 100.0,
                        }
                        for center, width_tenths, strength_centi in peaks
                    ],
                    "timestamp": time.time(),
                }
                with self._lock:
                    self._latest = payload
                    self._latest_binary = binary
                self.status.sequence = sequence
                self.status.level_db = round(level_db, 1)
                self.status.last_frame_at = payload["timestamp"]

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._latest) if self._latest else None

    def latest_binary(self) -> bytes | None:
        with self._lock:
            return self._latest_binary
