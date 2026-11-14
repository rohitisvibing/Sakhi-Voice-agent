"""Render the first-sign-in welcome call once, in the agent's own voice.

    uv run python scripts/render_welcome.py

Writes frontend/public/welcome/{1,2,3}.m4a (AAC via macOS afconvert; WAV elsewhere) and
welcome.json with each line's text and duration. Costs about ₹1, once; nothing per user.
"""

import asyncio
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.providers.tts.base import Voice
from app.providers.tts.sarvam import SarvamTTS

LINES = [
    "Hi, I'm Envoy.",
    ("Tell me what you need, a lower rent, a fair fare, a booking, "
     "and I'll make the call for you."),
    "Your first mission is on me.",
]
OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "welcome"


async def render(tts: SarvamTTS, voice: Voice, text: str) -> bytes:
    return b"".join([chunk async for chunk in tts.synthesize(text, voice)])


async def main() -> None:
    s = get_settings()
    if not s.sarvam_api_key:
        sys.exit("Set SARVAM_API_KEY in .env first.")
    tts = SarvamTTS(s)
    voice = Voice(speaker=s.sarvam_tts_agent_speaker, language_code="en-IN")
    OUT.mkdir(parents=True, exist_ok=True)
    aac = shutil.which("afconvert") is not None
    lines = []
    for i, text in enumerate(LINES, 1):
        pcm = await render(tts, voice, text)
        wav_path = OUT / f"{i}.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(tts.sample_rate)
            w.writeframes(pcm)
        name = wav_path.name
        if aac:
            m4a = OUT / f"{i}.m4a"
            await asyncio.to_thread(subprocess.run, [
                "afconvert", "-f", "m4af", "-d", "aac", "-b", "64000", str(wav_path), str(m4a)],
                check=True)
            wav_path.unlink()
            name = m4a.name
        duration = len(pcm) / 2 / tts.sample_rate
        lines.append({"src": f"/welcome/{name}", "text": text, "duration": round(duration, 2)})
        print(f"{name}: {duration:.1f}s  {text}")
    (OUT / "welcome.json").write_text(json.dumps({"lines": lines}, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
