import io
import json
import os
import uuid
import wave
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from anthropic import Anthropic
from groq import Groq
from piper import PiperVoice

from hal_prompt import HAL_SYSTEM_PROMPT

load_dotenv()

MODEL_PATH = "models/hal.onnx"
CLAUDE_MODEL = "claude-sonnet-4-6"
WHISPER_MODEL = "whisper-large-v3-turbo"
MAX_HISTORY_TURNS = 20
PROFILE_PATH = Path("profile.md")
DATA_DIR = Path(os.environ.get("HAL_DATA_DIR", "data"))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

print("Loading HAL voice...")
VOICE = PiperVoice.load(MODEL_PATH)
print("HAL voice loaded")

if PROFILE_PATH.exists():
    profile_text = PROFILE_PATH.read_text().strip()
    SYSTEM_PROMPT = f"{HAL_SYSTEM_PROMPT}\n\n---\n\nContext about Peter:\n\n{profile_text}"
    print(f"Loaded profile ({len(profile_text)} chars)")
else:
    SYSTEM_PROMPT = HAL_SYSTEM_PROMPT

groq_client = Groq()
anthropic_client = Anthropic()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


def session_file(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def load_history(session_id: str) -> list[dict]:
    f = session_file(session_id)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_history(session_id: str, history: list[dict]) -> None:
    tmp = session_file(session_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history))
    tmp.replace(session_file(session_id))


def transcribe(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    result = groq_client.audio.transcriptions.create(
        file=(filename, audio_bytes),
        model=WHISPER_MODEL,
        language="en",
    )
    return result.text.strip()


def hal_respond(history: list[dict]) -> str:
    resp = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=history,
    )
    return resp.content[0].text.strip()


def synthesize_hal(text: str) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        VOICE.synthesize_wav(text, wav_file)
    return buf.getvalue()


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.post("/api/talk")
async def talk(request: Request, audio: UploadFile = File(...)):
    session_id = request.cookies.get("hal_session")
    new_session = session_id is None
    if new_session:
        session_id = str(uuid.uuid4())
    history = load_history(session_id)

    audio_bytes = await audio.read()
    filename = audio.filename or "audio.webm"
    user_text = transcribe(audio_bytes, filename)

    if not user_text:
        resp = Response(status_code=204)
        if new_session:
            resp.set_cookie("hal_session", session_id, httponly=True, samesite="lax")
        return resp

    history.append({"role": "user", "content": user_text})
    trimmed = history[-MAX_HISTORY_TURNS:]

    hal_text = hal_respond(trimmed)
    history.append({"role": "assistant", "content": hal_text})
    save_history(session_id, history)

    wav_bytes = synthesize_hal(hal_text)

    resp = Response(content=wav_bytes, media_type="audio/wav")
    resp.headers["X-User-Transcript"] = quote(user_text)
    resp.headers["X-Hal-Transcript"] = quote(hal_text)
    if new_session:
        resp.set_cookie("hal_session", session_id, httponly=True, samesite="lax")
    return resp
