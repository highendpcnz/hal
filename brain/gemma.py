"""Local OpenAI-compatible Gemma conversation and read-only tool loop."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import BrainProviderError, KeyedLocks
from .events import EventHub
from .stopwords import is_stop_command


FAILURE_LINE = "I'm sorry, Dave. My local reasoning engine is unavailable."
_SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

# Gemma 4's chat template deliberately opens no new '<|turn>model' header for an
# assistant reply that *follows a tool response* -- that reply continues the model
# turn the tool call already opened (finetune/chat_template.jinja's
# `continue_same_model_turn`), and the generation-prompt branch mirrors it. Training
# and inference agree, so this is not a template mismatch -- but with no header to
# anchor it, the model has to produce the turn boundary itself, and confirmed live
# on the Pixel (fine-tuned model, reasoning=off) it sometimes gets it wrong: three
# identical requests returned "model\nI can see...", "HAL measured. The wall is...",
# and once the raw template continuation
# '...{"ultrasonic_cm":299}<tool|>user\nHow far away is the wall in front of you?'
# -- Dave's own question, which would then be spoken back at him.
#
# Strip the benign speaker label; treat leaked template structure as a failed
# generation rather than reading it aloud. Third-person narration ("HAL measured")
# is deliberately NOT rewritten here -- that is a model-quality gap for the next
# fine-tune, and regexing pronouns would corrupt legitimate replies.
_LEADING_ROLE_RE = re.compile(r"^(?:model|assistant)\s*[:\n]\s*", re.IGNORECASE)
# Both the well-formed markers and the garbled approximations the model actually
# produces. All of these were seen live from the fine-tuned model at reasoning=off,
# each a mangled '<|tool_call>call:NAME{...}<tool_call|>' that llama.cpp could not
# parse, so it surfaced as ordinary content and would have been read aloud:
#     <tool|>user\nHow far away is the wall in front of you?
#     <tool:read_spatial_sensors{}</tool>
#     <tool:call:emergency_stop{description:<|"|>stop immediat...
#     <function_call:emergency_stop{description:<|"|
# The last two are the dangerous ones -- that is an emergency stop that did not
# fire. Sanitizing cannot make the stop happen; it only keeps the wreckage out of
# Dave's ears. The underlying miss is a dataset-coverage gap (finetune/README.md).
_TEMPLATE_TOKEN_RE = re.compile(
    r"<\|?/?(?:tool_response|tool_call|function_call|function|turn|channel|tool)\b[:|>{]"
    r"|</(?:tool_response|tool_call|function_call|function|turn|channel|tool)>"
)


def _sanitize_reply(content: str) -> str:
    """Drop turn-marker artifacts; reject replies that leaked template structure."""
    text = _LEADING_ROLE_RE.sub("", content.strip(), count=1).strip()
    if _TEMPLATE_TOKEN_RE.search(text):
        raise BrainProviderError("Gemma leaked chat-template structure into its reply")
    if not text:
        raise BrainProviderError("Gemma returned an empty response")
    return text


_READ_SPATIAL_SENSORS = {
    "type": "function",
    "function": {
        "name": "read_spatial_sensors",
        "description": (
            "Read current ultrasonic distance, battery, attitude, and encoder state. "
            "This is read-only and does not move the robot."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
_CAPTURE_VISUAL_SCENE = {
    "type": "function",
    "function": {
        "name": "capture_visual_scene",
        "description": (
            "Capture a single still frame from the forward camera and view it. "
            "Use this when Dave asks what you can see. This is read-only and does "
            "not move the robot."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}
_DRIVE_STRAIGHT = {
    "type": "function",
    "function": {
        "name": "drive_straight",
        "description": (
            "Drive the robot in a straight line. Positive distance_cm is forward, "
            "negative is backward. Bounded and safety-checked before anything moves — "
            "forward motion is refused if an obstacle is too close. Use small, "
            "deliberate distances only when Dave has actually asked the robot to move."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "distance_cm": {
                    "type": "integer",
                    "description": "Distance to travel in centimeters. Positive is forward, negative is backward.",
                    "minimum": -50,
                    "maximum": 50,
                },
                "speed_pct": {
                    "type": "integer",
                    "description": "Motor speed as a percentage of maximum.",
                    "minimum": 1,
                    "maximum": 30,
                },
            },
            "required": ["distance_cm", "speed_pct"],
            "additionalProperties": False,
        },
    },
}
_TURN = {
    "type": "function",
    "function": {
        "name": "turn",
        "description": (
            "Rotate the robot in place. Positive angle_degrees turns right, negative "
            "turns left. Bounded and safety-checked before anything moves. Use only when "
            "Dave has actually asked the robot to turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "angle_degrees": {
                    "type": "integer",
                    "description": "Angle to rotate in degrees.",
                    "minimum": -180,
                    "maximum": 180,
                },
                "speed_pct": {
                    "type": "integer",
                    "description": "Motor speed as a percentage of maximum.",
                    "minimum": 1,
                    "maximum": 30,
                },
            },
            "required": ["angle_degrees", "speed_pct"],
            "additionalProperties": False,
        },
    },
}
_EMERGENCY_STOP = {
    "type": "function",
    "function": {
        "name": "emergency_stop",
        "description": (
            "Immediately stop all motors. Use this if Dave asks you to stop, or if "
            "anything about a move seems wrong."
        ),
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
}


class GemmaProvider:
    """Gemma via llama-server or Ollama's OpenAI-compatible chat endpoint."""

    name = "gemma"
    mode = "local-http"
    permission_mode = "deny"

    def __init__(
        self,
        events: EventHub,
        *,
        request_json: Callable[[dict], dict] | None = None,
        capture_frame: Callable[[], tuple[bytes, int, int]] | None = None,
    ) -> None:
        self.events = events
        self.endpoint = os.environ.get(
            "HAL_GEMMA_URL", "http://127.0.0.1:8080/v1/chat/completions"
        )
        self.model = os.environ.get("HAL_GEMMA_MODEL", "gemma-4-e2b")
        self.api_key = os.environ.get("HAL_GEMMA_API_KEY", "").strip()
        self.timeout = float(os.environ.get("HAL_GEMMA_TIMEOUT", "180"))
        self.max_tool_rounds = int(os.environ.get("HAL_GEMMA_MAX_TOOL_ROUNDS", "4"))
        # Safety default is ON. Set HAL_STOP_INTERCEPT=0 only to study the
        # model's own unaided stop behaviour -- never on a robot that can move.
        self.stop_intercept = os.environ.get("HAL_STOP_INTERCEPT", "1").strip() != "0"
        self.robot_port = os.environ.get("HAL_ROBOT_PORT", "/dev/ttyACM0")
        self.agent_cwd = os.path.expanduser(
            os.environ.get("HAL_AGENT_CWD", str(Path(__file__).resolve().parent.parent))
        )
        # The vision tool is only offered once a multimodal projector is actually
        # loaded — an image_url content part sent to a text-only endpoint either
        # errors or is silently ignored, so an unusable tool must not be advertised.
        self.vision_enabled = bool(os.environ.get("HAL_GEMMA_MMPROJ", "").strip())
        self.camera_device = os.environ.get("HAL_CAMERA_DEVICE", "0")
        self.camera_width = int(os.environ.get("HAL_CAMERA_WIDTH", "640"))
        self.camera_height = int(os.environ.get("HAL_CAMERA_HEIGHT", "480"))
        self.camera_timeout = float(os.environ.get("HAL_CAMERA_TIMEOUT", "5"))
        self.camera_rotate = int(os.environ.get("HAL_CAMERA_ROTATE", "2"))
        self.ffmpeg_bin = os.environ.get("HAL_FFMPEG_BIN", "ffmpeg")
        self.termux_camera_bin = os.environ.get("HAL_TERMUX_CAMERA_BIN", "termux-camera-photo")
        self._request_json = request_json or self._post_json
        self._capture_frame = capture_frame or self._capture_frame_auto
        self._data_dir: Path | None = None
        self._session_dir: Path | None = None
        self._system_prompt = (Path(__file__).with_name("GEMMA_SYSTEM.md")).read_text().strip()
        self._locks = KeyedLocks()
        self._active: dict[str, asyncio.Task] = {}
        self._file_lock = threading.Lock()
        self._last_error: str | None = None
        self._reachable = False

    def init(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._session_dir = data_dir / "brain" / "gemma"
        self._session_dir.mkdir(parents=True, exist_ok=True)

    async def startup(self) -> None:
        try:
            await asyncio.to_thread(self._probe)
        except BrainProviderError as error:
            self._reachable = False
            self._last_error = str(error)

    async def shutdown(self) -> None:
        for task in list(self._active.values()):
            task.cancel()
        self._active.clear()

    async def ask(self, text: str, session_id: str) -> str:
        if self._session_dir is None:
            raise BrainProviderError("Gemma provider has not been initialized")
        self._validate_session_id(session_id)
        async with self._locks.hold(session_id):
            task = asyncio.current_task()
            if task is not None:
                self._active[session_id] = task
            try:
                messages = self._load_messages(session_id)
                messages.append({"role": "user", "content": text})
                if self.stop_intercept and is_stop_command(text):
                    reply = await self._intercept_stop(session_id)
                else:
                    reply = await self._complete(messages, session_id)
                messages.append({"role": "assistant", "content": reply})
                self._save_messages(session_id, messages[-40:])
                self.events.commentary(session_id, reply)
                return reply
            except asyncio.CancelledError:
                raise
            except (BrainProviderError, OSError, ValueError) as error:
                self._last_error = str(error)
                print(f"[gemma] turn failed: {error}")
                return FAILURE_LINE
            finally:
                if task is not None and self._active.get(session_id) is task:
                    self._active.pop(session_id, None)

    async def _complete(self, messages: list[dict], session_id: str) -> str:
        tools = [_READ_SPATIAL_SENSORS, _DRIVE_STRAIGHT, _TURN, _EMERGENCY_STOP]
        if self.vision_enabled:
            tools.append(_CAPTURE_VISUAL_SCENE)
        working = [{"role": "system", "content": self._system_prompt}, *messages]
        for _round in range(self.max_tool_rounds + 1):
            payload = {
                "model": self.model,
                "messages": working,
                "tools": tools,
                "tool_choice": "auto",
                "temperature": 0.2,
            }
            response = await asyncio.to_thread(self._request_json, payload)
            message = self._response_message(response)
            self._reachable = True
            self._last_error = None
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = message.get("content")
                if not isinstance(content, str) or not content.strip():
                    raise BrainProviderError("Gemma returned an empty response")
                return _sanitize_reply(content)
            working.append(message)
            for call in tool_calls:
                result, extra_messages = await self._execute_tool(call, session_id)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or "tool"),
                        "content": json.dumps(result, separators=(",", ":")),
                    }
                )
                working.extend(extra_messages)
        raise BrainProviderError("Gemma exceeded the tool-call round limit")

    async def _intercept_stop(self, session_id: str) -> str:
        """Stop the robot without consulting the model.

        Deliberately bypasses `_complete` entirely: no inference, no tool-call
        parsing, no network. See brain/stopwords.py for why the one command that
        exists to halt a moving machine is not left to a sampled model.

        Events are published with the same shape the normal tool loop uses, so
        the UI, history and `/api/systems` cannot tell the difference -- the only
        observable difference is that it is fast and it always happens.
        """
        call_id = "stop_intercept"
        self.events.publish(
            session_id,
            {"type": "tool_call", "tool_call_id": call_id, "title": "emergency_stop", "status": "running"},
        )
        result = await asyncio.to_thread(self._emergency_stop)
        ok = bool(result.get("ok"))
        self.events.publish(
            session_id,
            {
                "type": "tool_call_update",
                "tool_call_id": call_id,
                "title": "emergency_stop",
                "status": "completed" if ok else "failed",
            },
        )
        if ok:
            return "Stopped."
        # Honest failure, in the same register the relay_failure training uses:
        # never report a stop that did not happen.
        return f"I couldn't stop the motors, Dave — {result.get('error', 'the robot did not respond')}."

    async def _execute_tool(self, call: dict, session_id: str) -> tuple[dict, list[dict]]:
        function = call.get("function") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        call_id = str(call.get("id") or "tool")
        self.events.publish(
            session_id,
            {"type": "tool_call", "tool_call_id": call_id, "title": name, "status": "running"},
        )
        extra_messages: list[dict] = []
        if name == "read_spatial_sensors":
            result = await asyncio.to_thread(self._read_spatial_sensors)
        elif name == "capture_visual_scene" and self.vision_enabled:
            result, extra_messages = await asyncio.to_thread(self._capture_visual_scene)
        elif name in ("drive_straight", "turn", "emergency_stop"):
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            if name == "drive_straight":
                result = await asyncio.to_thread(
                    self._drive_straight, arguments.get("distance_cm"), arguments.get("speed_pct")
                )
            elif name == "turn":
                result = await asyncio.to_thread(
                    self._turn, arguments.get("angle_degrees"), arguments.get("speed_pct")
                )
            else:
                result = await asyncio.to_thread(self._emergency_stop)
        else:
            result = {"ok": False, "error": f"unsupported tool: {name}"}
        status = "completed" if result.get("ok") else "failed"
        self.events.publish(
            session_id,
            {
                "type": "tool_call_update",
                "tool_call_id": call_id,
                "title": name,
                "status": status,
            },
        )
        return result, extra_messages

    def _open_telemetry_client(self):
        from robot.telemetry import CyberPiTelemetryClient

        # On Android/Termux there is no pyserial-visible device node — `termux-usb
        # -E` hands over an already-open USB fd via this env var instead (see
        # docs/termux-usb-bringup.md). Everywhere else (Mac/Linux dev, bench),
        # HAL_ROBOT_PORT is a normal pyserial device path.
        usb_fd = os.environ.get("TERMUX_USB_FD", "").strip()
        if usb_fd:
            from robot.android_usb import Ch340UsbTransport

            return CyberPiTelemetryClient(Ch340UsbTransport(int(usb_fd)))
        return CyberPiTelemetryClient.open(self.robot_port)

    def _open_robot_transport(self):
        """Open a raw transport to the CyberPi, Android USB fd or pyserial —
        same branching as `_open_telemetry_client`, but returning the bare
        transport so a drive/turn tool call can read a fresh obstacle-distance
        sample and then send a motion command over the *same* connection.
        Two separate connections to the same serial device fail with
        "Resource busy" (confirmed live), so this cannot be two calls into
        `_open_telemetry_client`/a motion-client opener."""

        usb_fd = os.environ.get("TERMUX_USB_FD", "").strip()
        if usb_fd:
            from robot.android_usb import Ch340UsbTransport

            return Ch340UsbTransport(int(usb_fd))
        try:
            import serial
        except ImportError as error:
            raise RuntimeError(
                "pyserial is required for hardware access; install requirements.txt"
            ) from error
        from robot.cyberpi import BAUD_RATE

        transport = serial.Serial(self.robot_port, BAUD_RATE, timeout=0.05, write_timeout=1.0)
        transport.reset_input_buffer()
        return transport

    def _drive_straight(self, distance_cm: object, speed_pct: object) -> dict:
        return self._run_motion(lambda client: client.drive_straight(distance_cm, speed_pct))

    def _turn(self, angle_degrees: object, speed_pct: object) -> dict:
        return self._run_motion(lambda client: client.turn(angle_degrees, speed_pct))

    def _run_motion(self, act) -> dict:
        """Shared plumbing for drive_straight/turn: open one transport, take a
        fresh telemetry sample for the safety interlock, arm, then act()
        immediately — the safety watchdog is 250ms and must not lapse against
        anything slower than local Python between arming and the motion call
        itself (see robot/safety.py)."""

        from robot.motion import CyberPiMotionClient
        from robot.protocol import Telemetry
        from robot.safety import MotionLimits, SafetyController
        from robot.telemetry import CyberPiTelemetryClient

        try:
            transport = self._open_robot_transport()
        except Exception as error:
            return {"ok": False, "error": str(error)}
        try:
            telemetry_client = CyberPiTelemetryClient(transport)
            telemetry_client.initialize()
            snapshot = telemetry_client.read_snapshot()

            safety = SafetyController(MotionLimits())
            motion_client = CyberPiMotionClient(transport, safety)
            motion_client.initialize()

            telemetry = Telemetry(
                left_ticks=0,
                right_ticks=0,
                yaw_deg=snapshot.yaw_deg,
                pitch_deg=snapshot.pitch_deg,
                obstacle_dist_cm=snapshot.ultrasonic_cm,
                battery_volts=0.0,
            )
            safety.connect()
            safety.arm()
            safety.update_telemetry(telemetry)
            act(motion_client)
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}
        finally:
            transport.close()

    def _emergency_stop(self) -> dict:
        from robot.estop import CyberPiEmergencyStopClient

        try:
            transport = self._open_robot_transport()
        except Exception as error:
            return {"ok": False, "error": str(error)}
        try:
            client = CyberPiEmergencyStopClient(transport)
            client.initialize()
            client.stop_all()
            return {"ok": True}
        except Exception as error:
            return {"ok": False, "error": str(error)}
        finally:
            transport.close()

    def _read_spatial_sensors(self) -> dict:
        try:
            with self._open_telemetry_client() as client:
                bring_up = client.initialize()
                snapshot = client.read_snapshot()
            return {
                "ok": True,
                "bring_up": asdict(bring_up),
                "telemetry": {
                    **asdict(snapshot),
                    "motors_stationary": snapshot.motors_stationary,
                    "ultrasonic_out_of_range": snapshot.ultrasonic_out_of_range,
                },
            }
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _capture_frame_auto(self) -> tuple[bytes, int, int]:
        # `termux-camera-photo` only exists in a real Termux install (see
        # docs/termux-port-status.md) — its presence on PATH is the same kind
        # of real-capability signal `_open_robot_transport` uses TERMUX_USB_FD
        # for, rather than a separate config flag to keep in sync by hand.
        import shutil

        if shutil.which(self.termux_camera_bin):
            return self._capture_frame_termux()
        return self._capture_frame_ffmpeg()

    def _capture_frame_termux(self) -> tuple[bytes, int, int]:
        from robot.camera import capture_frame_termux

        return capture_frame_termux(
            camera_id=self.camera_device,
            width=self.camera_width,
            height=self.camera_height,
            timeout=self.camera_timeout,
            termux_camera_bin=self.termux_camera_bin,
            ffmpeg_bin=self.ffmpeg_bin,
            rotate=self.camera_rotate,
        )

    def _capture_frame_ffmpeg(self) -> tuple[bytes, int, int]:
        from robot.camera import capture_frame

        return capture_frame(
            device=self.camera_device,
            width=self.camera_width,
            height=self.camera_height,
            timeout=self.camera_timeout,
            ffmpeg_bin=self.ffmpeg_bin,
        )

    def _capture_visual_scene(self) -> tuple[dict, list[dict]]:
        from robot.camera import CameraCaptureError

        try:
            image_bytes, width, height = self._capture_frame()
        except CameraCaptureError as error:
            return {"ok": False, "error": str(error)}, []
        name = f"capture-{int(time.time() * 1000)}.jpg"
        if self._data_dir is not None:
            viewscreen_dir = self._data_dir / "viewscreen"
            viewscreen_dir.mkdir(parents=True, exist_ok=True)
            (viewscreen_dir / name).write_bytes(image_bytes)
        # The base64 payload is large and only useful to the model for this one
        # turn, so it rides along as a synthetic user message (see _complete)
        # rather than in the tool result that gets persisted with the session.
        data_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
        result = {"ok": True, "path": name, "width": width, "height": height, "bytes": len(image_bytes)}
        extra_messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Captured frame from capture_visual_scene, attached below."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        return result, extra_messages

    def _post_json(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                decoded = json.loads(response.read())
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise BrainProviderError(f"Gemma HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise BrainProviderError(f"cannot reach local Gemma endpoint: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise BrainProviderError("Gemma endpoint returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise BrainProviderError("Gemma endpoint returned a non-object response")
        return decoded

    def _probe(self) -> None:
        suffix = "/v1/chat/completions"
        base = self.endpoint[: -len(suffix)] if self.endpoint.endswith(suffix) else self.endpoint
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        request = Request(f"{base.rstrip('/')}/v1/models", headers=headers, method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout, 5.0)) as response:
                if response.status != 200:
                    raise BrainProviderError(f"Gemma readiness returned HTTP {response.status}")
                json.loads(response.read())
        except HTTPError as error:
            raise BrainProviderError(f"Gemma readiness returned HTTP {error.code}") from error
        except URLError as error:
            raise BrainProviderError(f"cannot reach local Gemma endpoint: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise BrainProviderError("Gemma readiness returned invalid JSON") from error
        self._reachable = True
        self._last_error = None

    @staticmethod
    def _response_message(response: dict) -> dict:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise BrainProviderError("Gemma response has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise BrainProviderError("Gemma response has no assistant message")
        return message

    def _session_path(self, session_id: str) -> Path:
        assert self._session_dir is not None
        return self._session_dir / f"{session_id}.json"

    def _load_messages(self, session_id: str) -> list[dict]:
        try:
            value = json.loads(self._session_path(session_id).read_text())
        except (OSError, json.JSONDecodeError):
            return []
        return value if isinstance(value, list) else []

    def _save_messages(self, session_id: str, messages: list[dict]) -> None:
        path = self._session_path(session_id)
        temporary = path.with_suffix(".json.tmp")
        with self._file_lock:
            temporary.write_text(json.dumps(messages, indent=1))
            temporary.replace(path)

    def drop_session(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        try:
            self._session_path(session_id).unlink()
        except FileNotFoundError:
            pass
        self.events.drop_session(session_id)

    async def cancel(self, session_id: str) -> None:
        task = self._active.get(session_id)
        if task is not None:
            task.cancel()

    def health(self) -> dict:
        return {
            "provider": self.name,
            "mode": self.mode,
            "alive": self._reachable,
            "endpoint": self.endpoint,
            "model": self.model,
            "vision": self.vision_enabled,
            "last_error": self._last_error,
        }

    async def commands(self, _session_id: str) -> list[dict[str, str | None]]:
        return []

    def provider_session_for(self, session_id: str) -> str | None:
        if self._session_dir is None or not _SESSION_RE.fullmatch(session_id):
            return None
        return session_id if self._session_path(session_id).exists() else None

    async def run_diagnostic_command(self, args: list[str], _timeout: float) -> dict:
        command = args[0] if args else "status"
        if command == "status":
            value: object = self.health()
        elif command == "sessions":
            value = sorted(path.stem for path in (self._session_dir or Path()).glob("*.json"))
        elif command == "tools":
            value = [
                "read_spatial_sensors (read-only)",
                "drive_straight (motion, bounded and safety-checked)",
                "turn (motion, bounded and safety-checked)",
                "emergency_stop (motion, stops all motors)",
            ]
            if self.vision_enabled:
                value.append("capture_visual_scene (read-only)")
        elif command == "prompt-size":
            value = {
                "characters": len(self._system_prompt),
                "estimated_tokens": round(len(self._system_prompt) / 4),
            }
        elif command in {"skills", "mcp"}:
            value = ["Not used by the local Gemma runtime."]
        elif command == "logs":
            value = {"last_error": self._last_error}
        else:
            return {"ok": False, "code": None, "text": f"Unknown diagnostic: {command}"}
        return {"ok": True, "code": 0, "text": json.dumps(value, indent=2)}

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not _SESSION_RE.fullmatch(session_id):
            raise BrainProviderError("invalid brain session id")
