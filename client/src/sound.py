from __future__ import annotations

import threading

import winsound


def _play_beep(freq: int, duration_ms: int) -> None:
    try:
        winsound.Beep(int(freq), int(duration_ms))
    except Exception:
        winsound.MessageBeep()


def _play_wav(path: str) -> None:
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        winsound.MessageBeep()


def play_sound(spec: dict) -> None:
    kind = spec.get("kind", "beep")

    def runner():
        if kind == "wav":
            _play_wav(spec.get("path", ""))
        else:
            _play_beep(
                spec.get("freq", 880),
                spec.get("duration_ms", 180),
            )

    threading.Thread(target=runner, daemon=True).start()
