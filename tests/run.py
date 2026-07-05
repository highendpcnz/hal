#!/usr/bin/env python
"""Zero-dependency tests for the pure-python parts of the HAL frontend.

Run with the Hermes venv (no pytest required):

    ~/hermes-agent/.venv/bin/python tests/run.py

HAL_SKIP_MODELS=1 is set below, so the STT/TTS models never load — the whole
suite finishes in seconds and touches no audio, network, or inference.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["HAL_SKIP_MODELS"] = "1"
_tmp = tempfile.TemporaryDirectory(prefix="hal-tests-")
os.environ["HAL_DATA_DIR"] = _tmp.name

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
import hermes_bridge  # noqa: E402
import mission_control  # noqa: E402

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok  {name}")
    else:
        FAILURES.append(name)
        print(f"FAIL  {name}  {detail}")


# --- speakable -------------------------------------------------------------

md = (
    "Run `ls` now.\n\n```py\nx=1\n```\n\n| a | b |\n| 1 | 2 |\n\n> quoted\n\n"
    "---\n\n1. one\n- two\n**bold** ~~struck~~ [link](http://example.com) \U0001F680"
)
out = main.speakable(md)
check("speakable strips inline code ticks", "`" not in out, out)
check("speakable replaces code fences", "x=1" not in out and "transcript" in out, out)
check("speakable collapses tables", "|" not in out, out)
check("speakable strips blockquote marker", ">" not in out and "quoted" in out, out)
check("speakable strips emphasis", "*" not in out and "~" not in out, out)
check("speakable keeps link text, drops target", "http" not in out and "link" in out, out)
check("speakable strips list markers", "1." not in out and "one" in out and "two" in out, out)
check("speakable strips emoji", "\U0001F680" not in out, out)
check("speakable never returns empty", main.speakable("---").strip() != "")
check("speakable truncates", len(main.speakable("word " * 2000)) <= main.MAX_SPOKEN_CHARS + 100)

# --- session ids and history -----------------------------------------------

check("session id accepts uuid-ish", main._valid_session_id("abc-123_X.z") == "abc-123_X.z")
check("session id rejects slash", main._valid_session_id("../etc") is None)
check("session id rejects empty", main._valid_session_id("") is None and main._valid_session_id(None) is None)
check("session id rejects overlong", main._valid_session_id("a" * 129) is None)

try:
    main.session_file("../evil")
    check("session_file rejects traversal", False)
except ValueError:
    check("session_file rejects traversal", True)

sid = "test-hist"
main.save_history(sid, [{"role": "user", "content": "hi"}])
check("history roundtrip", main.load_history(sid) == [{"role": "user", "content": "hi"}])
main.session_file(sid).write_text("{not json")
check("history tolerates corrupt file", main.load_history(sid) == [])
main.session_file(sid).write_text('{"a": 1}')
check("history tolerates wrong shape", main.load_history(sid) == [])
check("history missing file", main.load_history("nope-xyz") == [])

# --- turn response headers ---------------------------------------------------

resp = main.Response()
main._apply_turn_headers(resp, "sid", False, "u" * 5000, "h" * 5000, {"infer": 12})
check(
    "transcript headers capped",
    len(resp.headers["X-Hal-Transcript"]) <= main.MAX_TRANSCRIPT_HEADER_CHARS * 3 + 16,
)
check("server timing header", "infer;dur=12" in resp.headers["Server-Timing"])
check("sample rate fallback without model", main.SAMPLE_RATE == 22050)

# --- CLI output cleanup ------------------------------------------------------

raw = "\x1b[31mred\x1b[0m\n\n\n\n" + str(Path.home()) + "/x\n" + "y" * 20000
cli = main._clean_cli_text(raw)
check("cli strips ansi", "\x1b" not in cli and "red" in cli)
check("cli shortens home dir", "~/x" in cli)
check("cli truncates", len(cli) <= main.MAX_CLI_OUTPUT_CHARS + 32 and "[truncated]" in cli)
check("cli empty placeholder", main._clean_cli_text("\x1b[2J") == "(no output)")

# --- hermes_bridge helpers ---------------------------------------------------

hosts = hermes_bridge._parse_check_hosts("1.1.1.1:443, example.com , [::1]:8080, bad:port")
check(
    "offline host parsing",
    ("1.1.1.1", 443) in hosts and ("example.com", 443) in hosts and ("::1", 8080) in hosts
    and len(hosts) == 3,
    repr(hosts),
)

smp = Path(_tmp.name) / "map-test.json"
sm = hermes_bridge.SessionMap(smp)
sm.set("c1", "h1")
sm.set("c2", "h2")
sm.drop("c1")
sm2 = hermes_bridge.SessionMap(smp)
check("session map persists", sm2.get("c2") == "h2" and sm2.get("c1") is None)

# --- keyed session locks -------------------------------------------------------
# Overlapping turns on one session must serialize, and eviction must not drop
# a lock that still has waiters (the old locked() check raced exactly there).


async def _exercise_keyed_locks():
    locks = hermes_bridge._KeyedLocks()
    active = 0
    max_active = 0

    async def turn():
        nonlocal active, max_active
        async with locks.hold("sess"):
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.005)
            active -= 1

    await asyncio.gather(*(turn() for _ in range(4)))
    return max_active, locks


_max_active, _keyed = asyncio.run(_exercise_keyed_locks())
check("keyed locks serialize a session", _max_active == 1, str(_max_active))
check(
    "keyed locks evict only when idle",
    _keyed._locks == {} and _keyed._refs == {},
    repr((_keyed._locks, _keyed._refs)),
)

# --- SSE event aliasing (missions) -------------------------------------------

q = hermes_bridge.register_event_queue("browser-1")
hermes_bridge.alias_events("mission-sess", "browser-1")
hermes_bridge.publish_event("mission-sess", {"type": "tool_call", "title": "probe"})
check("aliased publish reaches target queue", not q.empty() and "probe" in q.get_nowait())
hermes_bridge.unalias_events("mission-sess")
hermes_bridge.publish_event("mission-sess", {"type": "tool_call"})
check("unaliased publish goes nowhere", q.empty())
hermes_bridge.publish_event("browser-1", {"type": "mission_update"})
check("direct publish still works", not q.empty())
hermes_bridge.unregister_event_queue("browser-1", q)

# --- mission triggers ---------------------------------------------------------

check("mission trigger typed", main._mission_request("/mission fix the CI") == "fix the CI")
check("mission trigger typed empty", main._mission_request("/mission") == "")
check(
    "mission trigger voice",
    main._mission_request("HAL, start mission clean the pod bay.") == "clean the pod bay",
)
check(
    "mission trigger voice no comma",
    main._mission_request("hal start mission scan logs") == "scan logs",
)
check("mission trigger negative", main._mission_request("Hal, what's a mission?") is None)
check("plain text is not a mission", main._mission_request("open the pod bay doors") is None)

# --- mission persistence --------------------------------------------------------

mdir = Path(_tmp.name) / "missions-data"
mgr = mission_control.MissionManager(mdir)
mission = mission_control.Mission(id="m1", title="probe pod", cookie_id="c1", session_id="s1")
mgr._save(mission)
check("mission write leaves no tmp file", not list(mgr.missions_dir.glob("*.tmp")))
done = mission_control.Mission(
    id="m2", title="done", cookie_id="c2", session_id="s2", status="completed", result="ok"
)
mgr._save(done)
(mgr.missions_dir / "corrupt.json").write_text("{not json")
mgr2 = mission_control.MissionManager(mdir)
check("active mission is failed after restart", mgr2.missions["m1"].status == "failed")
check("completed mission survives restart", mgr2.missions["m2"].status == "completed")
check("corrupt mission file tolerated", len(mgr2.missions) == 2)
check(
    "list_missions filters by cookie",
    [m["id"] for m in mgr2.list_missions("c1")] == ["m1"],
)

# --- frontend duplex WS busy-state invariant ---------------------------------
# static/index.html has no JS test harness (zero-dependency Python suite), but
# a regression here is easy to reintroduce: if a duplex utterance starts
# recording (busy=true) without marking wsTurn, a socket drop mid-recording
# skips ws.onclose's unlock branch and wedges the UI (busy/isWsRecording stuck
# true forever, mic dead until reload). Assert the ordering statically instead.

_frontend_src = (Path(__file__).resolve().parent.parent / "static" / "index.html").read_text()


def _fn_body(name: str) -> str:
    start = _frontend_src.index(f"function {name}(")
    # crude brace match from the first '{' after the signature
    brace_start = _frontend_src.index("{", start)
    depth = 0
    for i in range(brace_start, len(_frontend_src)):
        if _frontend_src[i] == "{":
            depth += 1
        elif _frontend_src[i] == "}":
            depth -= 1
            if depth == 0:
                return _frontend_src[brace_start:i + 1]
    raise AssertionError(f"unbalanced braces scanning function {name}")


start_ws_recording = _fn_body("startWsRecording")
check(
    "startWsRecording marks wsTurn so a mid-recording disconnect can unlock",
    "wsTurn = true" in start_ws_recording,
    start_ws_recording,
)

ws_onclose_start = _frontend_src.index("ws.onclose = () => {")
ws_onclose_end = _frontend_src.index("};", ws_onclose_start)
ws_onclose_body = _frontend_src[ws_onclose_start:ws_onclose_end]
check(
    "ws.onclose stops a live duplex recorder before unlocking",
    "isWsRecording" in ws_onclose_body and "vadRecorder" in ws_onclose_body,
    ws_onclose_body,
)

# ----------------------------------------------------------------------------

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print(f"all tests passed")
