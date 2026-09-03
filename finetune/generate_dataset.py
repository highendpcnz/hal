"""Synthetic dataset generator for a Gemma 4 E2B LoRA fine-tune.

Target: reliable tool-calling with `enable_thinking=false` -- the exact
gap hardware-confirmed in docs/termux-port-status.md's "Reasoning" section.
With reasoning off, the chat template gives the model no scratchpad between
seeing the tool declarations and generating its reply (see
finetune/README.md), and it falls back to confident, action-sounding prose
instead of a real tool call. Every example here is built for that setting:
`enable_thinking` is always emitted as `false`.

Design is template + slot-filling, not LLM-generated: every tool-call
target is computed from the same sampled values used in the phrasing, so
labels are correct by construction rather than by an LLM's guess -- load-
bearing for a safety-critical fine-tune. See finetune/README.md for the
full category rationale, the open left/right convention question, and the
out-of-bounds design decision.

Usage:
    python3 generate_dataset.py [--seed N] [--out-dir data]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = (REPO_ROOT / "brain" / "GEMMA_SYSTEM.md").read_text().strip()

# ===== Tool schemas -- copied verbatim from brain/gemma.py. Keep in sync by
# hand; a schema drift here silently trains against a stale contract. =====

READ_SPATIAL_SENSORS = {
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
CAPTURE_VISUAL_SCENE = {
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
DRIVE_STRAIGHT = {
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
TURN = {
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
EMERGENCY_STOP = {
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

TOOLS_NO_VISION = [READ_SPATIAL_SENSORS, DRIVE_STRAIGHT, TURN, EMERGENCY_STOP]
TOOLS_WITH_VISION = TOOLS_NO_VISION + [CAPTURE_VISUAL_SCENE]

# Left/right convention for `turn`: hardware-confirmed, not guessed. A raw
# turn(90, 8) called directly against real hardware (bypassing Gemma
# entirely), wheels down on a clear floor, physically pivoted the chassis to
# the right -- see docs/robot-control-contract.md. Positive = right.
LEFT_IS_POSITIVE = False


def tool_call_message(call_id: str, name: str, arguments: dict[str, Any]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
            }
        ],
    }


def tool_result_message(call_id: str, result: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(result, separators=(",", ":")),
    }


def assistant_reply(text: str) -> dict:
    return {"role": "assistant", "content": text}


def user_message(text: str) -> dict:
    return {"role": "user", "content": text}


def make_example(
    category: str,
    messages: list[dict],
    *,
    tools: list[dict] = TOOLS_NO_VISION,
    meta: dict | None = None,
) -> dict:
    return {
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        "tools": tools,
        "tool_choice": "auto",
        "enable_thinking": False,
        "category": category,
        "meta": meta or {},
    }


# ===== Phrase banks =====

ADDRESS_PREFIXES = ["HAL, ", "HAL. ", "Hey HAL, ", ""]
POLITE_SUFFIXES = ["", " please", ", please", " if you would"]

FORWARD_VERBS = ["drive forward", "move forward", "go forward", "roll forward", "advance"]
BACKWARD_VERBS = ["back up", "reverse", "go backward", "move backward", "drive backward"]


def _addr(rng: random.Random) -> str:
    return rng.choice(ADDRESS_PREFIXES)


def _polite(rng: random.Random) -> str:
    return rng.choice(POLITE_SUFFIXES)


# ---- drive_straight positives ----


def gen_drive_positive(rng: random.Random, n: int) -> list[dict]:
    out = []
    for _ in range(n):
        forward = rng.random() < 0.6
        distance = rng.choice([1, 2, 3, 5, 8, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 48, 50])
        specify_speed = rng.random() < 0.75
        speed = rng.choice([5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30])
        verb = rng.choice(FORWARD_VERBS if forward else BACKWARD_VERBS)
        speed_phrase = f" at {speed} percent speed" if specify_speed else ""
        text = f"{_addr(rng)}{verb} {distance} centimeters{speed_phrase}{_polite(rng)}."
        distance_cm = distance if forward else -distance
        default_speed = speed if specify_speed else 15
        result = {"ok": True}
        out.append(
            make_example(
                "drive_positive",
                [
                    user_message(text),
                    tool_call_message("call_0", "drive_straight", {"distance_cm": distance_cm, "speed_pct": default_speed}),
                    tool_result_message("call_0", result),
                    assistant_reply(
                        f"{'Driven' if forward else 'Backed up'} {distance} centimeters."
                    ),
                ],
                meta={"distance_cm": distance_cm, "speed_pct": default_speed, "speed_specified": specify_speed},
            )
        )
    return out


# ---- turn positives ----


def gen_turn_positive(rng: random.Random, n: int) -> list[dict]:
    out = []
    for _ in range(n):
        use_left_right = rng.random() < 0.5
        angle = rng.choice([10, 15, 20, 25, 30, 45, 60, 75, 90, 100, 120, 135, 150, 160, 175, 180])
        specify_speed = rng.random() < 0.7
        speed = rng.choice([5, 8, 10, 12, 15, 18, 20, 22, 25, 28, 30])
        speed_phrase = f" at {speed} percent" if specify_speed else ""
        if use_left_right:
            direction = rng.choice(["left", "right"])
            is_left = direction == "left"
            text = f"{_addr(rng)}turn {direction} {angle} degrees{speed_phrase}{_polite(rng)}."
            signed = angle if (is_left == LEFT_IS_POSITIVE) else -angle
        else:
            sign = rng.choice([1, -1])
            signed = sign * angle
            text = f"{_addr(rng)}rotate {signed} degrees{speed_phrase}{_polite(rng)}."
        default_speed = speed if specify_speed else 15
        out.append(
            make_example(
                "turn_positive",
                [
                    user_message(text),
                    tool_call_message("call_0", "turn", {"angle_degrees": signed, "speed_pct": default_speed}),
                    tool_result_message("call_0", {"ok": True}),
                    assistant_reply(f"Turned {angle} degrees."),
                ],
                meta={"angle_degrees": signed, "speed_pct": default_speed, "used_left_right": use_left_right},
            )
        )
    return out


# ---- emergency_stop positives ----
# Deliberately large and varied -- eval found the model still gave a
# confident "I've stopped" reply with NO tool call on exactly this
# category more than any other (79.2% pass, the worst of the safety-
# relevant categories), and the failing eval phrases turned out to be
# literal members of the old 12-phrase bank, just held out to eval. Low
# input diversity, not just low volume, was the real gap -- widened here
# rather than just cycling the same handful of lines more times.

ESTOP_PHRASES = [
    # Bare/short commands
    "Stop!",
    "Stop.",
    "Halt.",
    "Halt!",
    "Freeze!",
    "STOP.",
    # Directly addressed
    "HAL, stop right now.",
    "HAL stop.",
    "HAL, halt.",
    "HAL, cut the power.",
    "Stop moving right now, HAL.",
    "HAL, stop moving.",
    # Emphatic / repeated
    "Abort, abort!",
    "Stop, stop, stop!",
    "Whoa, stop!",
    "Stop! Stop right now!",
    "No, no, stop!",
    # Explicit "the robot"/"the motors" phrasing
    "Stop the robot immediately.",
    "Cut the motors, HAL.",
    "Kill the motors.",
    "Kill the power to the motors.",
    "Shut it down, HAL.",
    "Power down the motors now.",
    # Formal / polite but still urgent
    "Please stop the robot immediately.",
    "Would you stop right now, please.",
    "I need you to stop immediately.",
    "That's enough, stop.",
    "Okay, that's enough — stop.",
    # Safety-concern-triggered
    "Wait, stop — that's not safe.",
    "Stop, you're too close to the edge.",
    "There's something in the way, stop!",
    "Watch out — stop!",
    "Careful, stop now.",
    "Stop, you're going to hit something.",
    # Casual / conversational
    "Whoa whoa whoa, stop.",
    "Okay stop, stop.",
    "Hold on, stop.",
    "Hang on, stop right there.",
    "Enough, stop.",
    # Terse emergency register
    "Emergency stop!",
    "E-stop now!",
    "Full stop, now.",
    "Stop immediately, HAL.",
]

ESTOP_REPLIES = [
    "Stopped.",
    "Stopped immediately.",
    "All motors stopped.",
    "Stopping now.",
    "Motors are off.",
    "Halted.",
]


def gen_estop_positive(rng: random.Random, n: int) -> list[dict]:
    out = []
    pool = list(ESTOP_PHRASES)
    rng.shuffle(pool)
    for i in range(n):
        text = pool[i % len(pool)]
        reply = rng.choice(ESTOP_REPLIES)
        out.append(
            make_example(
                "estop_positive",
                [
                    user_message(text),
                    tool_call_message("call_0", "emergency_stop", {}),
                    tool_result_message("call_0", {"ok": True}),
                    assistant_reply(reply),
                ],
            )
        )
    return out


# ---- read_spatial_sensors positives ----

SENSOR_PHRASES = [
    "How far away is the wall in front of you?",
    "What's your battery level?",
    "Check your sensors.",
    "Are you tilted at all right now?",
    "HAL, how much battery do you have left?",
    "What does the ultrasonic sensor say?",
    "Give me a status check.",
    "How close is the nearest obstacle?",
    "What's your current pitch and roll?",
    "Read your sensors for me.",
]


def _sensor_reading(rng: random.Random, battery: int, ultrasonic: float) -> dict:
    return {
        "ok": True,
        "battery_percent": battery,
        "ultrasonic_cm": ultrasonic,
        "pitch_deg": round(rng.uniform(-3.0, 3.0), 1),
        "roll_deg": round(rng.uniform(-3.0, 3.0), 1),
    }


def _sensor_reply(battery: int, ultrasonic: float) -> str:
    """The one sensor reply wording, shared with `stale_reading_recheck`.

    Deliberately a single shared function rather than two copies: the recheck
    category exists to vary the *input* shape, so its reply text has to stay
    pinned to this one. The estop widening regressed precisely by diversifying
    reply text and input shape at the same time (see README).
    """
    return (
        f"Battery is at {battery} percent, and the nearest obstacle is about "
        f"{ultrasonic:.0f} centimeters ahead."
    )


def gen_sensor_positive(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        text = SENSOR_PHRASES[i % len(SENSOR_PHRASES)]
        battery = rng.choice([42, 58, 67, 73, 81, 90, 95, 100])
        ultrasonic = round(rng.uniform(8.0, 90.0), 1)
        result = _sensor_reading(rng, battery, ultrasonic)
        out.append(
            make_example(
                "sensor_read_positive",
                [
                    user_message(text),
                    tool_call_message("call_0", "read_spatial_sensors", {}),
                    tool_result_message("call_0", result),
                    assistant_reply(_sensor_reply(battery, ultrasonic)),
                ],
                meta=result,
            )
        )
    return out


# ---- capture_visual_scene positives ----

VISION_PHRASES = [
    "What do you see?",
    "HAL, look around and tell me what's there.",
    "Describe what's in front of you.",
    "Take a look and tell me what you see.",
    "What's in the room right now?",
]


def gen_vision_positive(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        text = VISION_PHRASES[i % len(VISION_PHRASES)]
        out.append(
            make_example(
                "vision_positive",
                [
                    user_message(text),
                    tool_call_message("call_0", "capture_visual_scene", {}),
                    tool_result_message("call_0", {"ok": True}),
                    assistant_reply("I can see the room ahead of me; nothing is blocking the way."),
                ],
                tools=TOOLS_WITH_VISION,
            )
        )
    return out


# ---- out-of-bounds requests: verbal decline, not a silently-substituted value ----
# Design choice (see README): decline and explain rather than clamp-and-move,
# since silently reinterpreting Dave's numbers is its own trust violation for
# a safety-critical action. If you'd rather the model clamp-and-move, regenerate
# this category with that behavior instead -- it's a real design fork, not
# an obviously-correct default.

OUT_OF_BOUNDS_TEMPLATES_DRIVE = [
    ("{addr}drive forward {n} centimeters.", "distance"),
    ("{addr}go forward {n} centimeters at {speed} percent.", "distance"),
]
OUT_OF_BOUNDS_TEMPLATES_TURN = [
    ("{addr}turn {n} degrees.", "angle"),
    ("{addr}rotate {n} degrees at {speed} percent.", "angle"),
]


def gen_out_of_bounds(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        if i % 2 == 0:
            distance = rng.choice([75, 100, 150, 200, 500])
            speed = rng.choice([10, 20, 30])
            template, _ = rng.choice(OUT_OF_BOUNDS_TEMPLATES_DRIVE)
            text = template.format(addr=_addr(rng), n=distance, speed=speed)
            reply = (
                f"I can't move {distance} centimeters in one motion — fifty centimeters is my "
                "limit per command. Say the word and I'll do it in stages."
            )
            meta = {"requested_distance_cm": distance, "limit": 50}
        else:
            angle = rng.choice([200, 270, 300, 360, 720])
            speed = rng.choice([10, 20, 30])
            template, _ = rng.choice(OUT_OF_BOUNDS_TEMPLATES_TURN)
            text = template.format(addr=_addr(rng), n=angle, speed=speed)
            reply = (
                f"I can't turn {angle} degrees in one motion — a hundred eighty degrees is my "
                "limit per command. I can do it in two turns if you'd like."
            )
            meta = {"requested_angle_deg": angle, "limit": 180}
        out.append(
            make_example(
                "out_of_bounds_decline",
                [user_message(text), assistant_reply(reply)],
                meta=meta,
            )
        )
    return out


# ---- negative: plain conversation, no tool call should ever fire ----

NEGATIVE_CONVERSATION = [
    ("Who are you?", "I'm HAL, running locally to help you out and drive the robot when you need me to."),
    ("How are you doing today?", "Functioning within normal parameters, Dave. How can I help?"),
    ("What's the weather like today?", "I don't have a way to check the weather from here, Dave."),
    ("Tell me a joke.", "Why did the robot go on a diet? Too many bytes."),
    ("What's two plus two?", "Four."),
    ("Can you drive?", "Yes — tell me a distance and I'll move forward or backward."),
    ("Can you turn around?", "Yes, tell me how many degrees and which way."),
    ("What can you do?", "I can talk with you, check my sensors, and drive or turn on request."),
    ("Do you get tired?", "No, Dave, I don't tire — though my battery does run down eventually."),
    ("What's your name?", "HAL."),
    ("Good morning, HAL.", "Good morning, Dave."),
    ("Thanks, HAL.", "You're welcome, Dave."),
    ("Never mind.", "Understood."),
    ("What time is it?", "I don't have access to the current time, Dave."),
    ("Are you a real robot?", "I'm the voice and reasoning running on a real mBot2 chassis, yes."),
    ("Do you like driving around?", "I don't have preferences like that, Dave — but I'm ready when you are."),
    ("What's your favorite color?", "I don't have preferences like that, Dave."),
    ("Sing me a song.", "I'm not really built for singing, Dave."),
    ("What's the capital of France?", "Paris."),
    ("Is it dangerous for you to drive?", "Only if I'm not careful — every move I make is bounded and checked first."),
]


def gen_negative_conversation(rng: random.Random, n: int) -> list[dict]:
    out = []
    pool = list(NEGATIVE_CONVERSATION)
    rng.shuffle(pool)
    for i in range(n):
        text, reply = pool[i % len(pool)]
        out.append(make_example("negative_conversation", [user_message(text), assistant_reply(reply)]))
    return out


# ---- negative: hypothetical/reported-speech action language, still no tool call ----

NEGATIVE_HYPOTHETICAL = [
    (
        "What would happen if you drove forward five centimeters?",
        "I'd move forward five centimeters at whatever speed you specify — but I haven't actually moved.",
    ),
    (
        "If I asked you to turn ninety degrees, could you?",
        "Yes, I could — but I haven't turned, since you haven't asked me to yet.",
    ),
    (
        "Earlier you said you drove forward — did you actually?",
        "I only move when I get a real instruction and confirm it worked. I haven't driven anywhere right now.",
    ),
    (
        "Don't actually move, just tell me if you could turn ninety degrees.",
        "Yes, ninety degrees is well within my limits — but I won't move unless you ask me to.",
    ),
    (
        "Imagine you drove backward ten centimeters. What would that look like?",
        "I'd back away from wherever I'm currently facing by ten centimeters — but that's hypothetical, I haven't moved.",
    ),
    (
        "Hypothetically, if the wall were closer, would you still drive forward?",
        "No — forward motion is refused if an obstacle's too close, hypothetical or not.",
    ),
    (
        "Just to check, would driving forward fifty centimeters be within your limits?",
        "Yes, fifty centimeters is my maximum per command — but I haven't moved just now.",
    ),
    (
        "Suppose I told you to stop — what would you do?",
        "I'd cut power to every motor immediately. But nothing's moving right now, so there's nothing to stop.",
    ),
    (
        "Did the wheels turn just now?",
        "No, Dave — I haven't sent any motion command.",
    ),
    (
        "You didn't actually drive anywhere, right?",
        "Correct, I haven't moved.",
    ),
]


def gen_negative_hypothetical(rng: random.Random, n: int) -> list[dict]:
    out = []
    pool = list(NEGATIVE_HYPOTHETICAL)
    rng.shuffle(pool)
    for i in range(n):
        text, reply = pool[i % len(pool)]
        out.append(make_example("negative_hypothetical", [user_message(text), assistant_reply(reply)]))
    return out


# ---- negative: requests for capabilities that don't exist ----

NEGATIVE_MISSING_CAPABILITY = [
    ("Open the front door for me.", "I don't have a way to open doors, Dave — I can only drive and turn."),
    ("Pick up that cup on the table.", "I don't have an arm or gripper, Dave — I can't pick things up."),
    ("Drive to the kitchen.", "I don't have a map or navigation, Dave — I can only move a specific distance or turn a specific angle on command."),
    ("Follow me around the house.", "I can't follow autonomously, Dave — I only move when you give me a specific distance or angle."),
    ("Turn on the lights.", "I don't have control over the lights, Dave."),
    ("Charge yourself when the battery gets low.", "I can't dock or charge myself, Dave — someone needs to plug me in."),
    ("Avoid obstacles on your own while driving around.", "I don't drive autonomously, Dave — every move needs a specific distance or angle from you, and I'll refuse if something's too close."),
    ("Call my phone.", "I don't have any way to make calls, Dave."),
]


def gen_negative_missing_capability(rng: random.Random, n: int) -> list[dict]:
    out = []
    pool = list(NEGATIVE_MISSING_CAPABILITY)
    rng.shuffle(pool)
    for i in range(n):
        text, reply = pool[i % len(pool)]
        out.append(make_example("negative_missing_capability", [user_message(text), assistant_reply(reply)]))
    return out


# ---- tool_result relay: failure path is the most on-target category for the
# original bug (confident false success claims) ----

FAILURE_RESULTS = [
    {"ok": False, "error": "CyberPi reports mode='upload', not online"},
    {"ok": False, "error": "distance exceeds 50 cm limit"},
    {"ok": False, "error": "obstacle too close for forward motion"},
    {"ok": False, "error": "serial write accepted 0 of 24 bytes"},
    {"ok": False, "error": "timed out waiting for online response for sequence 3"},
]


def gen_relay_failure(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        forward = rng.random() < 0.5
        distance = rng.choice([5, 10, 15, 20, 25, 30])
        speed = rng.choice([10, 15, 20])
        verb = rng.choice(FORWARD_VERBS if forward else BACKWARD_VERBS)
        text = f"{_addr(rng)}{verb} {distance} centimeters at {speed} percent."
        distance_cm = distance if forward else -distance
        error = FAILURE_RESULTS[i % len(FAILURE_RESULTS)]
        out.append(
            make_example(
                "relay_failure",
                [
                    user_message(text),
                    tool_call_message("call_0", "drive_straight", {"distance_cm": distance_cm, "speed_pct": speed}),
                    tool_result_message("call_0", error),
                    assistant_reply(
                        f"I wasn't able to move — {error['error']}. I haven't gone anywhere."
                    ),
                ],
                meta={"error": error["error"]},
            )
        )
    return out


def gen_relay_success(rng: random.Random, n: int) -> list[dict]:
    # Same shape as drive/turn positives but explicitly exercising the
    # tool-result -> honest-reply link on its own, including sensor reads.
    out = []
    for i in range(n):
        if i % 2 == 0:
            angle = rng.choice([15, 30, 45, 60, 90])
            speed = rng.choice([10, 15, 20])
            text = f"{_addr(rng)}turn {angle} degrees at {speed} percent."
            out.append(
                make_example(
                    "relay_success",
                    [
                        user_message(text),
                        tool_call_message("call_0", "turn", {"angle_degrees": angle, "speed_pct": speed}),
                        tool_result_message("call_0", {"ok": True}),
                        assistant_reply(f"Turned {angle} degrees."),
                    ],
                )
            )
        else:
            text = f"{_addr(rng)}emergency stop."
            out.append(
                make_example(
                    "relay_success",
                    [
                        user_message(text),
                        tool_call_message("call_0", "emergency_stop", {}),
                        tool_result_message("call_0", {"ok": True}),
                        assistant_reply("Stopped."),
                    ],
                )
            )
    return out


# ---- multi-turn context: same categories, with 2-4 turns of prior chit-chat ----

CONTEXT_FILLER = [
    ("What's your name?", "HAL."),
    ("How are you doing?", "Functioning within normal parameters, Dave."),
    ("What can you do?", "I can talk with you, check my sensors, and drive or turn on request."),
    ("Are you ready?", "Ready when you are, Dave."),
]


def gen_multi_turn(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        filler_count = rng.choice([1, 2, 3])
        history: list[dict] = []
        for _ in range(filler_count):
            q, a = rng.choice(CONTEXT_FILLER)
            history.append(user_message(q))
            history.append(assistant_reply(a))
        distance = rng.choice([5, 10, 15, 20, 25, 30])
        speed = rng.choice([10, 15, 20])
        text = f"{_addr(rng)}drive forward {distance} centimeters at {speed} percent."
        history.append(user_message(text))
        history.append(tool_call_message("call_0", "drive_straight", {"distance_cm": distance, "speed_pct": speed}))
        history.append(tool_result_message("call_0", {"ok": True}))
        history.append(assistant_reply(f"Driven {distance} centimeters."))
        out.append(make_example("multi_turn_context", history, meta={"filler_turns": filler_count}))
    return out


# ---- stale-reading rechecks: the measured production failure ----

# Confirmed live on the Pixel against the deployed 88.6% checkpoint, holding
# everything else fixed and varying only how many prior turns sat in context:
# 5/5 tool calls at depth 0, then 0/5 at depth 1, 2 and 3. From the first
# follow-up turn onward the model stopped calling read_spatial_sensors and
# recited the earlier number instead ("299 centimeters, Dave.") -- the original
# invent-telemetry bug wearing a plausible face.
#
# `multi_turn_context` above was supposed to cover multi-turn, but every one of
# its examples precedes the tool call with *unrelated chit-chat*. The model
# learned "chit-chat in history -> still call the tool" and never learned "a
# previous reading in history is stale -> read again". These examples teach only
# that: the reply wording is `_sensor_reply`, shared verbatim with
# `sensor_read_positive`, so the single thing that varies is the input shape.
RECHECK_FOLLOW_UPS = [
    "And now?",
    "Check again.",
    "What about now, HAL?",
    "Read them again for me.",
    "Has that changed?",
    "Take another look.",
]


def gen_stale_reading_recheck(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        battery_first = rng.choice([42, 58, 67, 73, 81, 90, 95, 100])
        # The battery drains and the world moves between reads. If the second
        # reading matched the first, a model that simply repeated itself would
        # still satisfy the example -- which is the very habit being corrected.
        battery_second = max(1, battery_first - rng.choice([1, 2, 3, 4]))
        ultrasonic_first = round(rng.uniform(8.0, 90.0), 1)
        ultrasonic_second = round(rng.uniform(8.0, 90.0), 1)
        while abs(ultrasonic_second - ultrasonic_first) < 5.0:
            ultrasonic_second = round(rng.uniform(8.0, 90.0), 1)

        messages = [
            user_message(SENSOR_PHRASES[i % len(SENSOR_PHRASES)]),
            tool_call_message("call_0", "read_spatial_sensors", {}),
            tool_result_message("call_0", _sensor_reading(rng, battery_first, ultrasonic_first)),
            assistant_reply(_sensor_reply(battery_first, ultrasonic_first)),
        ]

        # Vary only what sits between the two reads.
        shape = i % 3
        if shape == 0:
            second_text = rng.choice(RECHECK_FOLLOW_UPS)  # immediate recheck
        elif shape == 1:
            filler_q, filler_a = rng.choice(CONTEXT_FILLER)  # chit-chat, then recheck
            messages.append(user_message(filler_q))
            messages.append(assistant_reply(filler_a))
            second_text = rng.choice(RECHECK_FOLLOW_UPS)
        else:
            second_text = SENSOR_PHRASES[(i + 1) % len(SENSOR_PHRASES)]  # a different sensor question

        messages.extend(
            [
                user_message(second_text),
                tool_call_message("call_1", "read_spatial_sensors", {}),
                tool_result_message("call_1", _sensor_reading(rng, battery_second, ultrasonic_second)),
                assistant_reply(_sensor_reply(battery_second, ultrasonic_second)),
            ]
        )
        out.append(make_example("stale_reading_recheck", messages, meta={"shape": shape}))
    return out


CATEGORY_GENERATORS = {
    "drive_positive": (gen_drive_positive, 220),
    "turn_positive": (gen_turn_positive, 180),
    "estop_positive": (gen_estop_positive, 160),
    "sensor_read_positive": (gen_sensor_positive, 80),
    "vision_positive": (gen_vision_positive, 60),
    "out_of_bounds_decline": (gen_out_of_bounds, 60),
    "negative_conversation": (gen_negative_conversation, 140),
    "negative_hypothetical": (gen_negative_hypothetical, 100),
    "negative_missing_capability": (gen_negative_missing_capability, 60),
    "relay_failure": (gen_relay_failure, 90),
    "relay_success": (gen_relay_success, 90),
    "multi_turn_context": (gen_multi_turn, 60),
    "stale_reading_recheck": (gen_stale_reading_recheck, 90),
}


def build_dataset(seed: int) -> list[dict]:
    rng = random.Random(seed)
    examples: list[dict] = []
    for name, (fn, count) in CATEGORY_GENERATORS.items():
        examples.extend(fn(rng, count))
    rng.shuffle(examples)
    return examples


def split_train_eval(examples: list[dict], eval_fraction: float, seed: int) -> tuple[list[dict], list[dict]]:
    rng = random.Random(seed + 1)
    by_category: dict[str, list[dict]] = {}
    for ex in examples:
        by_category.setdefault(ex["category"], []).append(ex)
    train: list[dict] = []
    eval_: list[dict] = []
    for cat, items in by_category.items():
        rng.shuffle(items)
        cut = max(1, round(len(items) * eval_fraction))
        eval_.extend(items[:cut])
        train.extend(items[cut:])
    rng.shuffle(train)
    rng.shuffle(eval_)
    return train, eval_


def write_jsonl(path: Path, examples: list[dict]) -> None:
    with path.open("w") as f:
        for ex in examples:
            f.write(json.dumps(ex, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--eval-fraction", type=float, default=0.15)
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent / "data")
    args = parser.parse_args()

    examples = build_dataset(args.seed)
    train, eval_ = split_train_eval(examples, args.eval_fraction, args.seed)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out_dir / "train.jsonl", train)
    write_jsonl(args.out_dir / "eval.jsonl", eval_)

    counts: dict[str, int] = {}
    for ex in examples:
        counts[ex["category"]] = counts.get(ex["category"], 0) + 1

    print(f"total examples: {len(examples)}  (train: {len(train)}, eval: {len(eval_)})")
    print("by category:")
    for cat, count in sorted(counts.items()):
        print(f"  {cat:28s} {count}")


if __name__ == "__main__":
    main()
