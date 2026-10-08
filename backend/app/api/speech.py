"""Text-to-speech for clients that cannot speak (Phase 18.1).

The Quest browser has no speechSynthesis, so Orbi was silent in the headset. The
server — the user's own computer — renders the reply with the operating system's
voice (macOS ``say``) and the headset plays the audio. Nothing leaves the machine.
Text is passed as an argument (never through a shell), capped in length, and results
are cached by content hash with a bounded cache.
"""
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter()
CACHE = Path(__file__).resolve().parents[3] / ".run" / "tts"
MAX_CHARS = 400
MAX_FILES = 200


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("say") is not None


@router.get("/speech")
def speech(text: str = Query(min_length=1, max_length=MAX_CHARS)):
    if not available():
        raise HTTPException(status_code=501, detail="no system voice on this server")
    clean = " ".join(text.replace("[[", "[").replace("]]", "]").split())  # no embedded `say` commands
    voice = os.environ.get("ORBIT_TTS_VOICE", "")
    key = hashlib.sha256(f"{voice}|{clean}".encode()).hexdigest()[:32]
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{key}.wav"
    if not out.exists():
        cmd = ["say", "-o", str(out), "--data-format=LEI16@22050"] + (["-v", voice] if voice else []) + ["--", clean]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=20)
        except (subprocess.SubprocessError, OSError) as exc:
            out.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"speech failed: {exc}")
        files = sorted(CACHE.glob("*.wav"), key=lambda f: f.stat().st_mtime)
        for old in files[:-MAX_FILES]:
            old.unlink(missing_ok=True)
    return FileResponse(out, media_type="audio/wav", headers={"Cache-Control": "private, max-age=86400"})
