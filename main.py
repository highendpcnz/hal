"""HAL 9000 voice frontend for Hermes Agent CLI.

Fork of https://huggingface.co/spaces/piclez/hal rewired to run fully local:
  - STT:   faster-whisper (bundled with the Hermes venv) instead of Groq
  - Brain: Hermes Agent CLI (named sessions, full tool access) instead of Claude
  - TTS:   campwill/HAL-9000-Piper-TTS with Hermes' HAL text normalization
           and optional ffmpeg mastering

Run with the Hermes venv:  ./run.sh   (or see README.md)
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import asyncio

from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from faster_whisper import WhisperModel
from piper import PiperVoice, SynthesisConfig
from pydantic import BaseModel

import hermes_bridge
from hermes_bridge import ask_hermes
import mission_control

APP_DIR = Path(__file__).resolve().parent
VOICE_PATH = Path(
    os.path.expanduser(os.environ.get("HAL_VOICE", "~/.hermes/voices/hal9000/hal9000.onnx"))
)
STT_MODEL_NAME = os.environ.get("HAL_STT_MODEL", "base.en")
STT_BEAM_SIZE = int(os.environ.get("HAL_STT_BEAM", "5"))
# Skip loading the STT/TTS models — for tests of the pure-python parts only;
# /api/talk and /api/say will not work.
SKIP_MODELS = os.environ.get("HAL_SKIP_MODELS", "") == "1"
# Optional bias prompt for whisper, e.g. "Dave speaking with HAL 9000." —
# helps it spell HAL/Hermes correctly. Off by default: a bias prompt can make
# whisper hallucinate text on near-silent recordings.
STT_PROMPT = os.environ.get("HAL_STT_PROMPT", "").strip() or None
MAX_HISTORY_TURNS = 40
MAX_SPOKEN_CHARS = 1500
# Transcripts travel in response headers; percent-encoding inflates ~3x and
# proxies/browsers cap header blocks, so bound them. Full text stays in history.
MAX_TRANSCRIPT_HEADER_CHARS = 2000
MAX_UPLOAD_BYTES = int(float(os.environ.get("HAL_MAX_UPLOAD_MB", "25")) * 1024 * 1024)
# Session cookie must outlive the browser process or the whole persistence
# story (ACP session/load, history files) dies with the window.
SESSION_COOKIE_MAX_AGE = int(float(os.environ.get("HAL_COOKIE_MAX_AGE_DAYS", "180")) * 86400)
SYSTEMS_CACHE_TTL = float(os.environ.get("HAL_SYSTEMS_TTL", "20"))
LATENCY_LOG = os.environ.get("HAL_LATENCY_LOG", "1").strip().lower() not in {"0", "false", "no"}
TTS_MASTERING = os.environ.get("HAL_TTS_MASTERING", "0").strip().lower() not in {
    "0",
    "false",
    "no",
}
MAX_CLI_OUTPUT_CHARS = int(os.environ.get("HAL_CLI_MAX_CHARS", "16000"))
HERMES_CLI_TIMEOUT = float(os.environ.get("HAL_CLI_TIMEOUT", "12"))
DATA_DIR = Path(os.environ.get("HAL_DATA_DIR", str(APP_DIR / "data")))
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
hermes_bridge.init(DATA_DIR)
mission_control.init(DATA_DIR)
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Reuse Hermes' HAL text normalization; ffmpeg mastering is optional for speed.
_HAL_TTS_SCRIPT = Path(
    os.path.expanduser(
        os.environ.get("HAL_TTS_SCRIPT", "~/.hermes/scripts/hal_piper_tts.py")
    )
)


def _load_hal_tts_module():
    spec = importlib.util.spec_from_file_location("hal_piper_tts", _HAL_TTS_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_hal_tts = _load_hal_tts_module() if _HAL_TTS_SCRIPT.exists() else None

SYN_CONFIG = SynthesisConfig(
    length_scale=float(os.environ.get("HAL_LENGTH_SCALE", "1.08")),
    noise_scale=float(os.environ.get("HAL_NOISE_SCALE", "0.6")),
    noise_w_scale=float(os.environ.get("HAL_NOISE_W_SCALE", "0.72")),
    normalize_audio=True,
)

if SKIP_MODELS:
    VOICE = None
    STT = None
else:
    print("Loading HAL voice...")
    VOICE = PiperVoice.load(str(VOICE_PATH))
    print("HAL voice loaded")
    print(f"Loading STT model ({STT_MODEL_NAME})...")
    STT = WhisperModel(STT_MODEL_NAME, device="cpu", compute_type="int8")
    print("STT model loaded")

SAMPLE_RATE = VOICE.config.sample_rate if VOICE is not None else 22050

# Piper phonemizes through espeak-ng, which keeps global state — concurrent
# synthesis from two turns must be serialized or it can crash/corrupt audio.
_TTS_LOCK = threading.Lock()

_BOOT_TIME = time.monotonic()

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await hermes_bridge.startup()
    yield
    await hermes_bridge.shutdown()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _valid_session_id(session_id: str | None) -> str | None:
    if session_id and _SESSION_ID_RE.fullmatch(session_id):
        return session_id
    return None


def _set_session_cookie(resp: Response, session_id: str) -> None:
    resp.set_cookie(
        "hal_session",
        session_id,
        max_age=SESSION_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
    )


def _session_from_request(request: Request) -> tuple[str, bool]:
    session_id = _valid_session_id(request.cookies.get("hal_session"))
    if session_id is not None:
        return session_id, False
    return str(uuid.uuid4()), True


def session_file(session_id: str) -> Path:
    if _valid_session_id(session_id) is None:
        raise ValueError("invalid session id")
    return SESSIONS_DIR / f"{session_id}.json"


def load_history(session_id: str) -> list[dict]:
    f = session_file(session_id)
    if f.exists():
        try:
            history = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        return history if isinstance(history, list) else []
    return []


def save_history(session_id: str, history: list[dict]) -> None:
    tmp = session_file(session_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(history))
    tmp.replace(session_file(session_id))


def transcribe(audio_bytes: bytes) -> str:
    segments, _info = STT.transcribe(
        io.BytesIO(audio_bytes),
        language="en",
        vad_filter=True,
        initial_prompt=STT_PROMPT,
        beam_size=STT_BEAM_SIZE,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


_MD_PATTERNS = [
    (re.compile(r"```.*?```", re.S), " I've put the code in the transcript, Dave. "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"(?:^[ \t]*\|.*\|[ \t]*$\n?)+", re.M), " The table is in the transcript, Dave. "),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^[ \t]*[-*_]{3,}[ \t]*$", re.M), ""),
    (re.compile(r"^\s*>\s?", re.M), ""),
    (re.compile(r"~~([^~]+)~~"), r"\1"),
    (re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"^\s*[-*•]\s+", re.M), ""),
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),
    # Emoji and dingbats — TTS either mangles them or reads their names aloud.
    (re.compile(r"[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]"), ""),
]


def speakable(text: str) -> str:
    """Strip anything TTS would mangle; the raw reply still goes to the log."""
    for pattern, repl in _MD_PATTERNS:
        text = pattern.sub(repl, text)
    if _hal_tts is not None:
        text = _hal_tts._normalize_hal_text(text)
    else:
        text = re.sub(r"\bHAL\b", "Hal", text)
    text = text[:MAX_SPOKEN_CHARS].strip()
    # Piper on an empty string is undefined behavior; never let it happen.
    return text or "The full response is in the transcript, Dave."


def synthesize_hal(text: str) -> bytes:
    buf = io.BytesIO()
    with _TTS_LOCK:
        with wave.open(buf, "wb") as wav_file:
            VOICE.synthesize_wav(text, wav_file, syn_config=SYN_CONFIG)
    raw = buf.getvalue()
    if _hal_tts is None or not TTS_MASTERING:
        return raw
    # Same subtle ffmpeg mastering chain Hermes' TTS provider applies.
    try:
        with tempfile.TemporaryDirectory(prefix="hal-web-") as tmp:
            raw_wav = Path(tmp) / "raw.wav"
            out_wav = Path(tmp) / "mastered.wav"
            raw_wav.write_bytes(raw)
            if _hal_tts._ffmpeg_master(raw_wav, out_wav):
                return out_wav.read_bytes()
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[tts] mastering skipped: {exc}")
    return raw


def synthesize_hal_stream(text: str):
    """Yield raw 16-bit mono PCM as Piper finishes each sentence — the browser
    starts playing after the first sentence instead of the whole reply.
    Starlette runs this sync generator in its threadpool."""
    with _TTS_LOCK:
        for chunk in VOICE.synthesize(text, syn_config=SYN_CONFIG):
            yield chunk.audio_int16_bytes


async def synthesize_hal_stream_async(text: str) -> AsyncIterator[bytes]:
    """synthesize_hal_stream for coroutines (the WebSocket paths): Piper runs
    in a worker thread, so synthesis never stalls the event loop the way
    iterating the sync generator directly in a coroutine would — that held
    _TTS_LOCK on the loop thread for the whole reply. If the consumer exits
    early (socket gone), the worker stops at the next sentence boundary."""
    loop = asyncio.get_running_loop()
    chunks: asyncio.Queue = asyncio.Queue()
    finished = object()
    abandoned = threading.Event()

    def produce() -> None:
        try:
            for chunk in synthesize_hal_stream(text):
                if abandoned.is_set():
                    break
                loop.call_soon_threadsafe(chunks.put_nowait, chunk)
        except Exception as exc:  # surfaced to the consumer below
            loop.call_soon_threadsafe(chunks.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(chunks.put_nowait, finished)

    producer = asyncio.create_task(asyncio.to_thread(produce))
    try:
        while True:
            item = await chunks.get()
            if item is finished:
                break
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        abandoned.set()
        await producer


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _log_latency(session_id: str, timings: dict[str, int]) -> None:
    if not LATENCY_LOG:
        return
    parts = " ".join(f"{name}={duration}ms" for name, duration in timings.items())
    print(f"[latency] session={session_id[:8]} {parts}")


async def run_turn_text(session_id: str, user_text: str) -> tuple[str, dict[str, int]]:
    """Inference + history — everything in a turn except audio synthesis."""
    timings: dict[str, int] = {}

    stage_start = time.perf_counter()
    hal_text = await ask_hermes(user_text, session_id)
    timings["infer"] = _elapsed_ms(stage_start)

    stage_start = time.perf_counter()
    history = load_history(session_id)
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": hal_text})
    save_history(session_id, history[-MAX_HISTORY_TURNS:])
    timings["history"] = _elapsed_ms(stage_start)
    return hal_text, timings


async def run_turn(session_id: str, user_text: str) -> tuple[str, bytes, dict[str, int]]:
    turn_start = time.perf_counter()
    hal_text, timings = await run_turn_text(session_id, user_text)

    stage_start = time.perf_counter()
    wav = await asyncio.to_thread(synthesize_hal, speakable(hal_text))
    timings["tts"] = _elapsed_ms(stage_start)
    timings["turn"] = _elapsed_ms(turn_start)
    return hal_text, wav, timings


def _clean_cli_text(text: str) -> str:
    text = _ANSI_RE.sub("", text)
    text = text.replace(str(Path.home()), "~")
    text = text.encode("ascii", "ignore").decode("ascii")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_CLI_OUTPUT_CHARS:
        text = text[:MAX_CLI_OUTPUT_CHARS].rstrip() + "\n[truncated]"
    return text or "(no output)"


async def _run_hermes_cli(args: list[str], timeout: float = HERMES_CLI_TIMEOUT) -> dict:
    env = {
        **os.environ,
        "TERM": "dumb",
        "NO_COLOR": "1",
        "CLICOLOR": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    try:
        proc = await asyncio.create_subprocess_exec(
            hermes_bridge.HERMES_BIN,
            *args,
            cwd=hermes_bridge.AGENT_CWD,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return {"ok": False, "code": None, "text": f"Hermes command unavailable: {exc}"}

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "code": None, "text": f"Hermes command timed out after {timeout:g}s."}

    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace")
    text = stdout if proc.returncode == 0 else "\n".join(part for part in (stdout, stderr) if part)
    return {"ok": proc.returncode == 0, "code": proc.returncode, "text": _clean_cli_text(text)}


_SYSTEM_SURFACES = {
    "sessions": (["sessions", "list", "--source", "hal-web", "--limit", "12"], 8),
    "status": (["status"], 8),
    "tools": (["tools", "list"], 8),
    "skills": (["skills", "list"], 8),
    "mcp": (["mcp", "list"], 8),
    "prompt": (["prompt-size"], 10),
    "logs": (["logs", "errors", "-n", "20", "--since", "24h"], 8),
}


def _apply_turn_headers(
    resp: Response,
    session_id: str,
    new_session: bool,
    user_text: str,
    hal_text: str,
    timings: dict[str, int] | None,
) -> Response:
    resp.headers["X-User-Transcript"] = quote(user_text[:MAX_TRANSCRIPT_HEADER_CHARS])
    resp.headers["X-Hal-Transcript"] = quote(hal_text[:MAX_TRANSCRIPT_HEADER_CHARS])
    if timings:
        resp.headers["Server-Timing"] = ", ".join(
            f"{name};dur={duration}" for name, duration in timings.items()
        )
        resp.headers["X-Hal-Timings"] = quote(json.dumps(timings, separators=(",", ":")))
    if new_session:
        _set_session_cookie(resp, session_id)
    return resp


def _turn_response(
    session_id: str,
    new_session: bool,
    user_text: str,
    hal_text: str,
    wav: bytes,
    timings: dict[str, int] | None = None,
) -> Response:
    resp = Response(content=wav, media_type="audio/wav")
    return _apply_turn_headers(resp, session_id, new_session, user_text, hal_text, timings)


def _stream_turn_response(
    session_id: str,
    new_session: bool,
    user_text: str,
    hal_text: str,
    timings: dict[str, int] | None = None,
) -> Response:
    """Headers go out immediately; PCM chunks follow as sentences synthesize."""
    resp = StreamingResponse(
        synthesize_hal_stream(speakable(hal_text)), media_type="audio/L16"
    )
    resp.headers["X-Hal-Sample-Rate"] = str(SAMPLE_RATE)
    return _apply_turn_headers(resp, session_id, new_session, user_text, hal_text, timings)


@app.get("/")
def index(request: Request):
    session_id, new_session = _session_from_request(request)
    resp = FileResponse(str(APP_DIR / "static" / "index.html"))
    if new_session:
        _set_session_cookie(resp, session_id)
    return resp


@app.get("/api/health")
def health():
    bridge = hermes_bridge.bridge_health()
    return {
        "status": "operational" if bridge["alive"] else "degraded",
        "voice": VOICE_PATH.name,
        "stt": STT_MODEL_NAME,
        "brain": f"hermes-agent ({hermes_bridge.BRIDGE_MODE})",
        "bridge": bridge,
    }


@app.get("/api/events")
async def events(request: Request):
    """SSE stream of tool-call/permission events for the caller's session —
    powers the diegetic eye tint and the tool-call ticker."""
    session_id = _valid_session_id(request.cookies.get("hal_session"))
    if session_id is None:
        return Response(status_code=204)

    async def gen():
        queue = hermes_bridge.register_event_queue(session_id)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            hermes_bridge.unregister_event_queue(session_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/status")
def status(request: Request):
    session_id = _valid_session_id(request.cookies.get("hal_session"))
    return {
        "session_id": session_id,
        "acp_session_id": hermes_bridge.acp_session_for(session_id) if session_id else None,
        "bridge_mode": hermes_bridge.BRIDGE_MODE,
        "bridge": hermes_bridge.bridge_health(),
        "yolo": hermes_bridge.YOLO,
        "voice": VOICE_PATH.name,
        "stt_model": STT_MODEL_NAME,
        "agent_cwd": hermes_bridge.AGENT_CWD,
        "uptime_seconds": round(time.monotonic() - _BOOT_TIME),
    }


# The surfaces fan out 7 CLI subprocesses; cache them briefly so reopening
# the drawer (or several tabs) doesn't hammer the Hermes CLI.
_systems_cache: dict | None = None
_systems_cache_at = 0.0
_systems_generated_at = 0
_systems_lock: asyncio.Lock | None = None


@app.get("/api/systems")
async def systems(request: Request, refresh: int = 0):
    global _systems_cache, _systems_cache_at, _systems_generated_at, _systems_lock
    if _systems_lock is None:
        _systems_lock = asyncio.Lock()
    session_id = _valid_session_id(request.cookies.get("hal_session"))
    async with _systems_lock:
        stale = _systems_cache is None or time.monotonic() - _systems_cache_at > SYSTEMS_CACHE_TTL
        if refresh or stale:
            tasks = {
                name: asyncio.create_task(_run_hermes_cli(args, timeout=timeout))
                for name, (args, timeout) in _SYSTEM_SURFACES.items()
            }
            _systems_cache = {name: await task for name, task in tasks.items()}
            _systems_cache_at = time.monotonic()
            _systems_generated_at = round(time.time())
        surfaces = _systems_cache
    return {
        "generated_at": _systems_generated_at,
        "local": {
            "session_id": session_id,
            "acp_session_id": hermes_bridge.acp_session_for(session_id) if session_id else None,
            "bridge_mode": hermes_bridge.BRIDGE_MODE,
            "yolo": hermes_bridge.YOLO,
            "voice": VOICE_PATH.name,
            "stt_model": STT_MODEL_NAME,
            "agent_cwd": Path(hermes_bridge.AGENT_CWD).name,
            "uptime_seconds": round(time.monotonic() - _BOOT_TIME),
        },
        "surfaces": surfaces,
    }


@app.get("/api/history")
def history(request: Request):
    session_id = _valid_session_id(request.cookies.get("hal_session"))
    if session_id is None:
        return {"history": []}
    return {"history": load_history(session_id)}


@app.post("/api/talk")
async def talk(request: Request, audio: UploadFile = File(...), stream: int = 0):
    total_start = time.perf_counter()
    timings: dict[str, int] = {}
    session_id, new_session = _session_from_request(request)

    stage_start = time.perf_counter()
    audio_chunks = []
    total_size = 0
    while True:
        chunk = await audio.read(65536)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_UPLOAD_BYTES:
            timings["upload"] = _elapsed_ms(stage_start)
            resp = Response(status_code=413)
            if new_session:
                _set_session_cookie(resp, session_id)
            return resp
        audio_chunks.append(chunk)
    audio_bytes = b"".join(audio_chunks)
    timings["upload"] = _elapsed_ms(stage_start)

    stage_start = time.perf_counter()
    user_text = await asyncio.to_thread(transcribe, audio_bytes)
    timings["stt"] = _elapsed_ms(stage_start)

    if not user_text:
        resp = Response(status_code=204)
        if new_session:
            _set_session_cookie(resp, session_id)
        return resp

    if stream:
        hal_text, turn_timings = await run_turn_text(session_id, user_text)
        timings.update(turn_timings)
        timings["total"] = _elapsed_ms(total_start)
        _log_latency(session_id, timings)
        return _stream_turn_response(session_id, new_session, user_text, hal_text, timings)

    hal_text, wav, turn_timings = await run_turn(session_id, user_text)
    timings.update(turn_timings)
    timings["total"] = _elapsed_ms(total_start)
    _log_latency(session_id, timings)
    return _turn_response(session_id, new_session, user_text, hal_text, wav, timings)


class SayRequest(BaseModel):
    text: str


@app.post("/api/say")
async def say(request: Request, body: SayRequest, stream: int = 0):
    """Text-in, voice-out — same pipeline as /api/talk minus the microphone."""
    total_start = time.perf_counter()
    session_id, new_session = _session_from_request(request)

    user_text = body.text.strip()
    if not user_text:
        resp = Response(status_code=204)
        if new_session:
            _set_session_cookie(resp, session_id)
        return resp

    if stream:
        hal_text, timings = await run_turn_text(session_id, user_text)
        timings["total"] = _elapsed_ms(total_start)
        _log_latency(session_id, timings)
        return _stream_turn_response(session_id, new_session, user_text, hal_text, timings)

    hal_text, wav, timings = await run_turn(session_id, user_text)
    timings["total"] = _elapsed_ms(total_start)
    _log_latency(session_id, timings)
    return _turn_response(session_id, new_session, user_text, hal_text, wav, timings)


@app.post("/api/session/reset")
def reset_session(request: Request):
    """Start fresh: drop the Hermes session mapping and transcript history,
    then hand the browser a new cookie."""
    old_id = _valid_session_id(request.cookies.get("hal_session"))
    if old_id is not None:
        hermes_bridge.drop_session(old_id)
        session_file(old_id).unlink(missing_ok=True)
    new_id = str(uuid.uuid4())
    resp = JSONResponse({"session_id": new_id})
    _set_session_cookie(resp, new_id)
    return resp


# ---------------------------------------------------------------------------
# Full-duplex WebSocket + mission notifications
# ---------------------------------------------------------------------------

# One live conversation socket per browser session. A newer tab replaces an
# older one; the older socket's cleanup must not evict its replacement.
active_websockets: dict[str, WebSocket] = {}

# Spoken mission trigger, e.g. "HAL, start mission tidy the downloads folder."
_MISSION_VOICE_RE = re.compile(r"^\s*hal[,.]?\s+start\s+mission[,:]?\s*(.*)$", re.I)


def _mission_request(user_text: str) -> str | None:
    """Return the mission title if the utterance starts one, else None.

    Typed form: "/mission <title>". Spoken form: "HAL, start mission <title>".
    An empty title means the trigger fired but no mission can be created.
    """
    if user_text == "/mission" or user_text.startswith("/mission "):
        return user_text[len("/mission"):].strip()
    match = _MISSION_VOICE_RE.match(user_text)
    if match is not None:
        return match.group(1).strip().rstrip(".!?")
    return None


async def _ws_send_tts(websocket: WebSocket, text: str) -> None:
    """Speak one reply over the socket: tts_start, PCM frames, tts_done."""
    await websocket.send_json({"type": "tts_start", "sample_rate": SAMPLE_RATE})
    async for chunk in synthesize_hal_stream_async(speakable(text)):
        await websocket.send_bytes(chunk)
    await websocket.send_json({"type": "tts_done"})


async def _ws_abort_turn(websocket: WebSocket, reason: str, text: str) -> None:
    """Tell the client this turn produced no reply so it can unlock its UI."""
    await websocket.send_json({"type": "turn_aborted", "reason": reason, "text": text})


async def _ws_run_turn(websocket: WebSocket, session_id: str, user_text: str) -> None:
    await websocket.send_json({"type": "transcript", "role": "user", "text": user_text})

    mission_title = _mission_request(user_text)
    if mission_title is None:
        hal_text, _timings = await run_turn_text(session_id, user_text)
    else:
        if mission_title:
            mission_control.manager.create_mission(
                session_id, mission_title, f"Execute mission: {mission_title}"
            )
            hal_text = (
                f"I've started the mission: {mission_title}. "
                "I will let you know when it is done."
            )
        else:
            hal_text = "I need a mission title, Dave. Tell me what the mission is."
        # run_turn_text records normal turns in the scrollback; mission
        # trigger turns belong there too.
        history = load_history(session_id)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": hal_text})
        save_history(session_id, history[-MAX_HISTORY_TURNS:])

    await websocket.send_json({"type": "transcript", "role": "hal", "text": hal_text})
    await _ws_send_tts(websocket, hal_text)


async def on_mission_complete(mission: mission_control.Mission) -> None:
    if mission.status == "failed":
        text = (
            f"Dave, I have completed the mission: {mission.title}. "
            f"Unfortunately, it failed. {mission.result}"
        )
    else:
        text = (
            f"Dave, I have completed the mission: {mission.title}. "
            "I'm ready to review the results with you."
        )

    # The report belongs in the scrollback whether or not anyone is listening.
    history = load_history(mission.cookie_id)
    history.append({"role": "assistant", "content": text})
    save_history(mission.cookie_id, history[-MAX_HISTORY_TURNS:])

    websocket = active_websockets.get(mission.cookie_id)
    if websocket is None:
        return
    try:
        await websocket.send_json({"type": "transcript", "role": "hal", "text": text})
        await _ws_send_tts(websocket, text)
    except Exception as exc:
        print(f"[ws] mission completion notify failed: {exc!r}")


mission_control.manager.on_complete = on_mission_complete


@app.websocket("/ws/conversation")
async def ws_conversation(websocket: WebSocket):
    session_id = _valid_session_id(websocket.cookies.get("hal_session"))
    if session_id is None:
        await websocket.close(code=1008, reason="Missing session cookie")
        return

    await websocket.accept()
    active_websockets[session_id] = websocket
    audio_chunks: list[bytes] = []
    audio_size = 0
    audio_overflow = False

    try:
        while True:
            msg = await websocket.receive()
            # Raw receive() reports disconnect as a message, not an exception —
            # calling receive() again after it raises RuntimeError.
            if msg.get("type") == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                if audio_overflow:
                    continue
                audio_size += len(msg["bytes"])
                if audio_size > MAX_UPLOAD_BYTES:
                    # Same cap as /api/talk — drop the recording, keep the socket.
                    audio_chunks = []
                    audio_overflow = True
                else:
                    audio_chunks.append(msg["bytes"])
            elif "text" in msg and msg["text"]:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")

                if kind == "start_speech":
                    audio_chunks = []
                    audio_size = 0
                    audio_overflow = False
                    continue

                if kind == "text_input":
                    user_text = (data.get("text") or "").strip()
                elif kind == "end_speech":
                    if audio_overflow:
                        audio_overflow = False
                        await _ws_abort_turn(
                            websocket, "too_long", "That recording was too long for me, Dave."
                        )
                        continue
                    audio_bytes = b"".join(audio_chunks)
                    audio_chunks = []
                    audio_size = 0
                    if not audio_bytes:
                        await _ws_abort_turn(
                            websocket, "no_speech", "I didn't quite catch that, Dave."
                        )
                        continue
                    user_text = await asyncio.to_thread(transcribe, audio_bytes)
                else:
                    continue

                if not user_text:
                    await _ws_abort_turn(
                        websocket, "no_speech", "I didn't quite catch that, Dave."
                    )
                    continue
                try:
                    await _ws_run_turn(websocket, session_id, user_text)
                except WebSocketDisconnect:
                    raise
                except Exception as exc:
                    print(f"[ws] turn failed (session={session_id[:8]}): {exc!r}")
                    await _ws_abort_turn(
                        websocket, "error", "Something went wrong on my end, Dave."
                    )
    except WebSocketDisconnect:
        pass
    finally:
        if active_websockets.get(session_id) is websocket:
            active_websockets.pop(session_id, None)
