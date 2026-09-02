"""On-device listen/speak loop for the Termux/Pixel deployment.

The Mac's browser-based push-to-talk and full-duplex WebSocket paths capture
audio client-side and upload it for main.py's transcribe() to decode, but
`termux-speech-to-text` has no such input — it always captures live from the
phone's own microphone and returns text directly, and Android's recognizer
only listens for a few seconds per call before giving up on silence (see
docs/termux-port-status.md). This is therefore a separate loop, not an
engine swap: it repeatedly listens with the phone's own mic, answers through
the same `run_turn()` pipeline `/api/say` already uses, and plays the reply
through the phone's own speaker — entirely apart from the browser-audio
endpoints, which keep working unchanged for the Mac.

Enabled only when HAL_TERMUX_LISTEN is truthy (see main.py's lifespan).

Gated by a wake word (HAL_WAKE_WORD, default "hal") — this loop otherwise
answers *anything* it hears near the phone with no addressing at all, which
during bring-up genuinely picked up unrelated real conversation nearby and
answered it. Set HAL_WAKE_WORD="" to disable the gate and go back to that
always-answering behavior.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import subprocess
import tempfile
import wave
from typing import Awaitable, Callable

SESSION_ID = "termux-onboard"
LISTEN_TIMEOUT_SECONDS = 30.0
# termux-media-player's `play` returns as soon as playback *starts*, not when
# it ends — without a wait matched to the clip's real length, the loop would
# start listening again while HAL is still talking and hear its own voice.
PLAYBACK_SETTLE_SECONDS = 0.4
WAKE_WORD = os.environ.get("HAL_WAKE_WORD", "hal").strip()


def _heard_wake_word(text: str, wake_word: str = WAKE_WORD) -> bool:
    """Whole-word, case-insensitive match — "hal" must not match inside
    "halt" or "shall". An empty wake_word disables the gate entirely (every
    utterance passes), matching how other HAL_* flags in this project treat
    an empty string as "off"."""

    if not wake_word:
        return True
    return re.search(rf"\b{re.escape(wake_word)}\b", text, re.IGNORECASE) is not None


async def listen_once(timeout: float = LISTEN_TIMEOUT_SECONDS) -> str:
    """One termux-speech-to-text call. Returns '' on silence/timeout/error —
    never raises, since a bad recognition should not kill the loop."""

    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            ["termux-speech-to-text"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        print(f"[termux-voice] listen failed: {error!r}")
        return ""
    if completed.returncode != 0:
        print(f"[termux-voice] termux-speech-to-text exited {completed.returncode}")
        return ""
    return completed.stdout.strip()


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    import io

    with wave.open(io.BytesIO(wav_bytes)) as wav_file:
        frames = wav_file.getnframes()
        rate = wav_file.getframerate()
    return frames / rate if rate else 0.0


async def speak(wav_bytes: bytes) -> None:
    """Play synthesized speech through the phone's speaker and block until
    it should be done, so the loop does not immediately re-listen over it."""

    duration = _wav_duration_seconds(wav_bytes)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(wav_bytes)
        path = Path(handle.name)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["termux-media-player", "play", str(path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[termux-voice] playback failed: {result.stderr.strip()}")
            return
        await asyncio.sleep(duration + PLAYBACK_SETTLE_SECONDS)
    finally:
        path.unlink(missing_ok=True)


RunTurn = Callable[..., Awaitable[tuple[str, bytes, dict]]]


async def listen_loop(run_turn: RunTurn) -> None:
    """Forever: listen with the phone's mic, answer via run_turn(), speak
    the reply. Runs as a background task started from main.py's lifespan;
    a stray exception from one turn must not end the loop."""

    print("[termux-voice] on-device listen loop starting")
    if WAKE_WORD:
        print(f"[termux-voice] wake word required: {WAKE_WORD!r}")
    else:
        print("[termux-voice] no wake word set — answering everything heard nearby")
    while True:
        try:
            user_text = await listen_once()
            if not user_text:
                continue
            if not _heard_wake_word(user_text):
                print(f"[termux-voice] ignored (no wake word): {user_text!r}")
                continue
            print(f"[termux-voice] heard: {user_text!r}")
            hal_text, wav, _timings = await run_turn(SESSION_ID, user_text)
            print(f"[termux-voice] replying: {hal_text!r}")
            await speak(wav)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a bad turn must not kill the loop
            print(f"[termux-voice] loop error: {error!r}")
            await asyncio.sleep(1.0)
