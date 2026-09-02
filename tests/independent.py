#!/usr/bin/env python3
"""Dependency-free tests for HAL brain contracts and the local Gemma tool loop."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.base import KeyedLocks  # noqa: E402
from brain.events import EventHub  # noqa: E402
from brain.gemma import GemmaProvider  # noqa: E402

os.environ.pop("HAL_BRAIN", None)
os.environ.pop("HAL_GEMMA_MMPROJ", None)
import brain.runtime as default_runtime  # noqa: E402


def check(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"  ok  {name}")


async def test_keyed_locks() -> None:
    locks = KeyedLocks()
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with locks.hold("session"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1

    await asyncio.gather(worker(), worker(), worker())
    check("brain sessions are single-writer", peak == 1 and not locks._locks)


async def test_events() -> None:
    events = EventHub()
    observed: list[tuple[str, dict]] = []
    events.observer = lambda session, payload: observed.append((session, payload))
    queue = events.register_queue("browser")
    events.alias("mission", "browser")
    events.publish("mission", {"type": "tool_call"})
    payload = json.loads(queue.get_nowait())
    check(
        "mission events route through HAL-owned aliases",
        payload["mission_session"] == "mission" and observed[0][0] == "browser",
    )
    events.unregister_queue("browser", queue)


async def test_gemma_tool_loop() -> None:
    requests: list[dict] = []

    def fake_request(payload: dict) -> dict:
        requests.append(payload)
        if len(requests) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "sensor-1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_spatial_sensors",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "The chassis is stationary, Dave."}}
            ]
        }

    with tempfile.TemporaryDirectory(prefix="hal-independent-") as temporary:
        events = EventHub()
        queue = events.register_queue("session-1")
        commentary: list[str] = []
        events.set_commentary_sink("session-1", commentary.append)
        provider = GemmaProvider(events, request_json=fake_request)
        provider.init(Path(temporary))
        provider._read_spatial_sensors = lambda: {
            "ok": True,
            "telemetry": {"motors_stationary": True, "ultrasonic_cm": 42.0},
        }

        reply = await provider.ask("Report spatial sensors.", "session-1")
        first_event = json.loads(queue.get_nowait())
        second_event = json.loads(queue.get_nowait())
        tool_names = [tool["function"]["name"] for tool in requests[0]["tools"]]
        serialized = json.dumps(requests)
        check("Gemma completes a local tool round trip", reply == "The chassis is stationary, Dave.")
        check("Gemma initially exposes only read-only sensors", tool_names == ["read_spatial_sensors"])
        check(
            "Gemma emits tool lifecycle events",
            first_event["status"] == "running" and second_event["status"] == "completed",
        )
        check("Gemma commentary uses the provider-neutral event hub", commentary == [reply])
        check(
            "Gemma prompt contains no motion tool",
            not any(name in serialized for name in ("drive_distance", "rotate_heading")),
        )
        check(
            "Gemma sessions persist under repository data",
            provider.provider_session_for("session-1") == "session-1",
        )
        provider.drop_session("session-1")
        check("Gemma session reset removes local model history", provider.provider_session_for("session-1") is None)


async def test_gemma_capture_visual_scene() -> None:
    requests: list[dict] = []

    def fake_request(payload: dict) -> dict:
        requests.append(payload)
        if len(requests) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "cam-1",
                                    "type": "function",
                                    "function": {"name": "capture_visual_scene", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "I see an empty workbench, Dave."}}
            ]
        }

    def fake_capture() -> tuple[bytes, int, int]:
        return b"\xff\xd8\xff\xd9", 640, 480

    with tempfile.TemporaryDirectory(prefix="hal-independent-") as temporary:
        events = EventHub()
        queue = events.register_queue("session-vision")
        provider = GemmaProvider(events, request_json=fake_request, capture_frame=fake_capture)
        provider.init(Path(temporary))
        provider.vision_enabled = True

        reply = await provider.ask("What do you see?", "session-vision")
        tool_names = [tool["function"]["name"] for tool in requests[0]["tools"]]
        second_round_messages = requests[1]["messages"]
        image_messages = [
            message
            for message in second_round_messages
            if isinstance(message.get("content"), list)
            and any(part.get("type") == "image_url" for part in message["content"])
        ]

        check(
            "capture_visual_scene completes a local tool round trip",
            reply == "I see an empty workbench, Dave.",
        )
        check(
            "vision tool is only offered once mmproj is configured",
            tool_names == ["read_spatial_sensors", "capture_visual_scene"],
        )
        check("a captured frame is attached to the model as an image message", len(image_messages) == 1)
        check(
            "the persisted session history carries no image data",
            "image_url" not in json.dumps(provider._load_messages("session-vision")),
        )
        written = list((Path(temporary) / "viewscreen").glob("*.jpg"))
        check("the captured frame is dropped onto the viewscreen for Dave", len(written) == 1)

        first_event = json.loads(queue.get_nowait())
        second_event = json.loads(queue.get_nowait())
        check(
            "capture_visual_scene emits tool lifecycle events",
            first_event["status"] == "running" and second_event["status"] == "completed",
        )


async def test_gemma_capture_visual_scene_failure() -> None:
    from robot.camera import CameraCaptureError

    requests: list[dict] = []

    def fake_request(payload: dict) -> dict:
        requests.append(payload)
        if len(requests) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "cam-1",
                                    "type": "function",
                                    "function": {"name": "capture_visual_scene", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "I can't reach the camera right now, Dave."}}
            ]
        }

    def failing_capture() -> tuple[bytes, int, int]:
        raise CameraCaptureError("ffmpeg not found: ffmpeg")

    with tempfile.TemporaryDirectory(prefix="hal-independent-") as temporary:
        events = EventHub()
        provider = GemmaProvider(events, request_json=fake_request, capture_frame=failing_capture)
        provider.init(Path(temporary))
        provider.vision_enabled = True

        reply = await provider.ask("What do you see?", "session-vision-fail")
        tool_message = [m for m in requests[1]["messages"] if m.get("role") == "tool"][-1]
        parsed = json.loads(tool_message["content"])
        image_present = any(
            isinstance(message.get("content"), list) for message in requests[1]["messages"]
        )

        check(
            "a capture failure still completes the turn",
            reply == "I can't reach the camera right now, Dave.",
        )
        check(
            "a capture failure reports ok:false without a viewscreen write",
            parsed == {"ok": False, "error": "ffmpeg not found: ffmpeg"},
        )
        check("no image message is attached after a failed capture", not image_present)
        check(
            "no file is dropped onto the viewscreen after a failed capture",
            not list((Path(temporary) / "viewscreen").glob("*.jpg")),
        )


async def main() -> None:
    project = Path(__file__).resolve().parent.parent
    check(
        "Gemma is the independent default provider",
        default_runtime.PROVIDER_NAME == "gemma" and "hermes_bridge" not in sys.modules,
    )
    check(
        "application layers import only the neutral brain runtime",
        all(
            "hermes_bridge" not in (project / name).read_text()
            for name in ("main.py", "mission_control.py")
        ),
    )
    await test_keyed_locks()
    await test_events()
    await test_gemma_tool_loop()
    await test_gemma_capture_visual_scene()
    await test_gemma_capture_visual_scene_failure()
    print("all independent tests passed")


if __name__ == "__main__":
    asyncio.run(main())
