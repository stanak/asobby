from __future__ import annotations

import math
import struct
import threading
from typing import Callable, Optional

try:
    import winsound

    WINSOUND_AVAILABLE = True
except ImportError:
    WINSOUND_AVAILABLE = False

_chime_wav: bytes | None = None
_play_lock = threading.Lock()


def _tone_samples(
    freq_hz: float,
    duration_sec: float,
    *,
    sample_rate: int,
    amplitude: float,
) -> list[int]:
    count = max(1, int(sample_rate * duration_sec))
    attack = max(1, int(sample_rate * 0.008))
    out: list[int] = []
    for i in range(count):
        t = i / sample_rate
        if i < attack:
            env = i / attack
        else:
            env = math.exp(-5.0 * (i - attack) / max(1, count - attack))
        sample = int(32767 * amplitude * env * math.sin(2 * math.pi * freq_hz * t))
        out.append(sample)
    return out


def _silence_samples(duration_sec: float, *, sample_rate: int) -> list[int]:
    return [0] * max(0, int(sample_rate * duration_sec))


def build_soft_chime_wav(*, sample_rate: int = 22050) -> bytes:
    """短い二音のソフトなチャイム WAV (PCM mono 16-bit)。"""
    amplitude = 0.11
    samples: list[int] = []
    samples.extend(
        _tone_samples(392.0, 0.07, sample_rate=sample_rate, amplitude=amplitude)
    )
    samples.extend(_silence_samples(0.025, sample_rate=sample_rate))
    samples.extend(
        _tone_samples(523.25, 0.09, sample_rate=sample_rate, amplitude=amplitude)
    )

    pcm = b"".join(struct.pack("<h", max(-32768, min(32767, s))) for s in samples)
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + pcm


def _chime_wav() -> bytes:
    global _chime_wav
    if _chime_wav is None:
        _chime_wav = build_soft_chime_wav()
    return _chime_wav


def play(*, log: Optional[Callable[[str], None]] = None) -> bool:
    """非同期で短い通知音を再生する。"""
    if not WINSOUND_AVAILABLE:
        if log:
            log("winsound が利用できないため通知音を再生できません")
        return False
    with _play_lock:
        try:
            winsound.PlaySound(
                _chime_wav(),
                winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
            return True
        except Exception as e:
            if log:
                log(f"通知音の再生に失敗: {e!r}")
            return False
