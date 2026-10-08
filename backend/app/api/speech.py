"""Orbi's speaking voice for clients that cannot speak (Phase 18.1, 19.1).

Two voices, best first: Groq's hosted Orpheus voice when GROQ_API_KEY is set (natural,
nothing runs here), else the operating system's voice (macOS ``say``). If Groq refuses
(terms not accepted, free limit reached) the system voice is used and Groq is retried
after a pause. Text goes as an argument, never through a shell; length is capped;
results are cached by content hash in a bounded cache.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.app.services.voice_agent import GROQ, http

router = APIRouter()
CACHE = Path(__file__).resolve().parents[3] / ".run" / "tts"
MAX_CHARS = 400
MAX_FILES = 200
GROQ_TTS = "canopylabs/orpheus-v1-english"
_groq_paused_until = 0.0
_groq_problem = ""


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("say") is not None


def groq_voice_ready() -> bool:
    return bool(os.environ.get("GROQ_API_KEY")) and time.time() >= _groq_paused_until


def voice_status() -> dict:
    return {"groq": bool(os.environ.get("GROQ_API_KEY")), "groq_ready": groq_voice_ready(), "groq_problem": _groq_problem,
            "system": available()}


def _groq(text: str, out: Path) -> bool:
    global _groq_paused_until, _groq_problem
    voice = os.environ.get("GROQ_VOICE", "troy")
    status, data = http(f"{GROQ}/audio/speech", {"model": GROQ_TTS, "input": text, "voice": voice, "response_format": "wav"},
                        {"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    if status == 200 and data[:4] == b"RIFF":
        out.write_bytes(data)
        _groq_problem = ""
        return True
    try:
        _groq_problem = json.loads(data)["error"]["message"][:200]
    except Exception:
        _groq_problem = f"HTTP {status}"
    _groq_paused_until = time.time() + 120  # use the system voice for a while, then retry
    return False


def _system(text: str, out: Path) -> None:
    voice = os.environ.get("ORBIT_TTS_VOICE", "")
    if re.search(r"[\u0900-\u097F]", text) and not voice:
        voice = "Lekha"  # Hindi text: the macOS Hindi voice
    cmd = ["say", "-o", str(out), "--data-format=LEI16@22050"] + (["-v", voice] if voice else []) + ["--", text]
    subprocess.run(cmd, check=True, capture_output=True, timeout=20)


@router.get("/speech")
def speech(text: str = Query(min_length=1, max_length=MAX_CHARS)):
    clean = " ".join(text.replace("[[", "[").replace("]]", "]").split())  # no embedded `say` commands
    use_groq = groq_voice_ready() and not re.search(r"[\u0900-\u097F]", clean)  # Orpheus speaks English
    if not use_groq and not available():
        raise HTTPException(status_code=501, detail="no voice available on this server")
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = f"groq:{os.environ.get('GROQ_VOICE', 'troy')}" if use_groq else f"say:{os.environ.get('ORBIT_TTS_VOICE', '')}"
    out = CACHE / f"{hashlib.sha256(f'{tag}|{clean}'.encode()).hexdigest()[:32]}.wav"
    if not out.exists():
        try:
            if not (use_groq and _groq(clean, out)):
                if not available():
                    raise HTTPException(status_code=502, detail=f"Groq voice failed: {_groq_problem}")
                _system(clean, out)
        except (subprocess.SubprocessError, OSError) as exc:
            out.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"speech failed: {exc}")
        files = sorted(CACHE.glob("*.wav"), key=lambda f: f.stat().st_mtime)
        for old in files[:-MAX_FILES]:
            old.unlink(missing_ok=True)
    return FileResponse(out, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})
