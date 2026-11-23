"""Keypad tones (DTMF) as PCM16, for pressing keys in a phone menu over the call audio.

Exotel's stream has no "send digit" message, so tones are played into the call ("in-band"),
which IVRs recognise the same way they recognise a handset's keypad.
"""

import math
import re
import struct

# A phone menu reading out options ("press 1 for English", "dial 2", "ಒತ್ತಿ", "दबाएं").
MENU_RE = re.compile(
    r"\b(press|dial|select|enter|type)\s+(\d|one|two|three|four|five|six|seven|eight|nine|"
    r"zero|star|hash)\b|\bfor .{1,30}\b(press|dial)\b|ಒತ್ತಿ|दबाएं|दबाइए|दबायें|"
    r"அழுத்தவும்|నొక్కండి", re.IGNORECASE)


def sounds_like_menu(text: str) -> bool:
    return bool(text) and MENU_RE.search(text) is not None

ROWS = {"1": 697, "2": 697, "3": 697, "A": 697, "4": 770, "5": 770, "6": 770, "B": 770,
        "7": 852, "8": 852, "9": 852, "C": 852, "*": 941, "0": 941, "#": 941, "D": 941}
COLS = {"1": 1209, "4": 1209, "7": 1209, "*": 1209, "2": 1336, "5": 1336, "8": 1336,
        "0": 1336, "3": 1477, "6": 1477, "9": 1477, "#": 1477, "A": 1633, "B": 1633,
        "C": 1633, "D": 1633}
VALID = set(ROWS) | {","}


def clean(digits: str | None) -> str:
    """Keep keypad characters (and ',' pauses) only."""
    return "".join(ch for ch in (digits or "").upper() if ch in VALID)


def _silence(rate: int, ms: int) -> bytes:
    return b"\x00\x00" * (rate * ms // 1000)


def tone(digit: str, rate: int = 16000, ms: int = 120, amplitude: float = 0.35) -> bytes:
    lo, hi = ROWS[digit], COLS[digit]
    n = rate * ms // 1000
    fade = max(1, rate // 500)  # 2 ms ramps avoid clicks that can read as extra presses
    out = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade)
        v = amplitude * env * (math.sin(2 * math.pi * lo * i / rate)
                               + math.sin(2 * math.pi * hi * i / rate)) / 2
        out += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
    return bytes(out)


def sequence(digits: str, rate: int = 16000, *, gap_ms: int = 120, pause_ms: int = 500) -> bytes:
    """Tones for `digits`, with ',' as a pause, padded so the IVR hears a clean start."""
    audio = bytearray(_silence(rate, 100))
    for ch in clean(digits):
        if ch == ",":
            audio += _silence(rate, pause_ms)
            continue
        audio += tone(ch, rate) + _silence(rate, gap_ms)
    return bytes(audio)
