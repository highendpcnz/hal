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

# --- speech truncation -------------------------------------------------------

check("truncate passes short text through", main._truncate_speech("Hello, Dave.", 100) == "Hello, Dave.")
long = ("One sentence here. " * 20).strip()
cut = main._truncate_speech(long, 100)
check("truncate ends on a sentence", cut.endswith("sentence here.") and len(cut) <= 100, cut)
nospace = "x" * 300
check("truncate survives no boundaries", len(main._truncate_speech(nospace, 100)) == 100)
words = "word " * 100
wcut = main._truncate_speech(words, 52)
check("truncate falls back to word boundary", wcut.endswith("word") and len(wcut) <= 52, wcut)

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
    locks = hermes_bridge.KeyedLocks()
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

# --- pending permission registry ----------------------------------------------


async def _exercise_permissions():
    req_id, fut = hermes_bridge._register_permission("owner-1", "Run ls")
    found = hermes_bridge.pending_permission_for("owner-1")
    none_found = hermes_bridge.pending_permission_for("owner-2")
    wrong_owner = hermes_bridge.resolve_permission(req_id, True, "owner-2")
    unknown = hermes_bridge.resolve_permission("nope", True, "owner-1")
    right = hermes_bridge.resolve_permission(req_id, True, "owner-1")
    value = await fut
    double = hermes_bridge.resolve_permission(req_id, False, "owner-1")
    hermes_bridge._pending_permissions.pop(req_id, None)
    return found == req_id, none_found is None, wrong_owner, unknown, right, value, double


_found, _none, _wrong, _unknown, _right, _value, _double = asyncio.run(_exercise_permissions())
check("pending permission is discoverable by owner", _found and _none)
check("foreign session cannot resolve a permission", not _wrong and not _unknown)
check("owner resolves the permission", _right and _value is True)
check("a resolved permission cannot be re-answered", not _double)

# --- spoken permission answers -------------------------------------------------

check("voice allow matches", main._PERM_ALLOW_RE.match("Yes.") is not None)
check("voice allow with address", main._PERM_ALLOW_RE.match("HAL, go ahead") is not None)
check("voice deny matches", main._PERM_DENY_RE.match("no") is not None)
check("voice deny abort", main._PERM_DENY_RE.match("Hal, abort!") is not None)
check("ordinary speech is not an answer",
      main._PERM_ALLOW_RE.match("yes we should think about this") is None
      and main._PERM_DENY_RE.match("no idea what that means") is None)

# --- wake-word gate --------------------------------------------------------------

check("wake matches plain address", main._WAKE_RE.match("HAL, open the log.") is not None)
check("wake matches hey-prefix", main._WAKE_RE.match("Hey HAL what's our status?") is not None)
_bare = main._WAKE_RE.match("Hal.")
check("bare HAL leaves empty remainder", _bare is not None and _bare.group(1).strip() == "")
check("wake rejects ambient speech", main._WAKE_RE.match("How are you doing?") is None)
check("wake rejects embedded hal", main._WAKE_RE.match("Halt the presses") is None)
check(
    "wake keeps the remainder",
    main._WAKE_RE.match("HAL, start mission scan logs").group(1).strip() == "start mission scan logs",
)

# --- SSE event aliasing (missions) -------------------------------------------

q = hermes_bridge.register_event_queue("browser-1")
hermes_bridge.alias_events("mission-sess", "browser-1")
hermes_bridge.publish_event("mission-sess", {"type": "tool_call", "title": "probe"})
_aliased = q.get_nowait() if not q.empty() else ""
check("aliased publish reaches target queue", "probe" in _aliased, _aliased)
check("aliased publish is tagged with its mission", '"mission_session": "mission-sess"' in _aliased.replace('":"', '": "'), _aliased)
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

# --- mission execution: cap, brain notes, failure journal ----------------------

import json  # noqa: E402
import mission_control as mc  # noqa: E402


async def _exercise_missions():
    orig_ask = hermes_bridge.ask_hermes

    async def fake_ask(text, sid):
        if "explode" in text:
            raise RuntimeError("boom")
        await asyncio.sleep(0.01)
        return "All wrapped up."

    hermes_bridge.ask_hermes = fake_ask
    try:
        mgr3 = mc.MissionManager(Path(_tmp.name) / "missions-exec")
        for i in range(mc.MAX_ACTIVE_MISSIONS):
            mgr3.create_mission("ck", f"m{i}", "do the thing")
        capped = False
        try:
            mgr3.create_mission("ck", "over-cap", "do the thing")
        except mc.MissionLimitError:
            capped = True
        mgr3.create_mission("ck2", "explode", "explode now")
        await asyncio.gather(*list(mgr3._tasks))
        notes = mgr3.drain_notes("ck")
        drained = mgr3.drain_notes("ck")
        fail_notes = mgr3.drain_notes("ck2")
        return mgr3, capped, notes, drained, fail_notes
    finally:
        hermes_bridge.ask_hermes = orig_ask


_mgr3, _capped, _notes, _drained, _fail_notes = asyncio.run(_exercise_missions())
check("mission cap enforced", _capped)
check(
    "missions complete with results and finish times",
    all(m.status == "completed" and m.finished_at for m in _mgr3.missions.values()
        if m.cookie_id == "ck"),
)
check(
    "completed missions queue brain notes",
    len(_notes) == mc.MAX_ACTIVE_MISSIONS and all("All wrapped up." in n for n in _notes),
    repr(_notes),
)
check("notes drain once", _drained == [])
check("failed mission queues a failure note", len(_fail_notes) == 1 and "failed" in _fail_notes[0])
_failed_journal = (_mgr3.missions_dir / "failed.jsonl").read_text().splitlines()
check(
    "failed mission journaled for reflection",
    len(_failed_journal) == 1 and json.loads(_failed_journal[0])["title"] == "explode",
)

# --- mission triggers -----------------------------------------------------------


async def _exercise_triggers():
    orig_ask = hermes_bridge.ask_hermes

    async def fake_ask(text, sid):
        return "trigger done"

    hermes_bridge.ask_hermes = fake_ask
    try:
        tdir = Path(_tmp.name) / "missions-trig"
        mgr4 = mc.MissionManager(tdir)
        watched = tdir / "watched"
        watched.mkdir()
        (tdir / "triggers.json").write_text(json.dumps([
            {"title": "hourly", "prompt": "check systems", "every_minutes": 1},
            {"title": "no prompt — skipped"},
            {"title": "watcher", "prompt": "scan downloads", "watch": str(watched / "*")},
            {"title": "off", "prompt": "x", "every_minutes": 1, "enabled": False},
        ]))
        state: dict = {}
        mgr4._scheduler_tick(state, now=1000.0)          # arms interval, baselines watch
        after_arm = len(mgr4.missions)
        mgr4._scheduler_tick(state, now=1000.0 + 61)     # interval fires
        (watched / "new.txt").write_text("hello")
        mgr4._scheduler_tick(state, now=1000.0 + 62)     # watcher fires
        mgr4._scheduler_tick(state, now=1000.0 + 63)     # nothing new — no refire
        await asyncio.gather(*list(mgr4._tasks))
        return mgr4, after_arm
    finally:
        hermes_bridge.ask_hermes = orig_ask


_mgr4, _after_arm = asyncio.run(_exercise_triggers())
_titles = sorted(m.title for m in _mgr4.missions.values())
check("triggers arm without a boot storm", _after_arm == 0)
check("interval and watch triggers fire exactly once", _titles == ["hourly", "watcher"], repr(_titles))
check(
    "trigger missions belong to the trigger cookie",
    all(m.cookie_id == mc.TRIGGER_COOKIE for m in _mgr4.missions.values()),
)
_watch_mission = next(m for m in _mgr4.missions.values() if m.title == "watcher")
check("watch trigger annotates its prompt", "(Trigger: files changed under" in _watch_mission.prompt)
check(
    "trigger missions visible to every session",
    len(_mgr4.list_missions("any-cookie")) == 2,
)
_tdir2 = Path(_tmp.name) / "missions-trig2"
_mgr5 = mc.MissionManager(_tdir2)
(_tdir2 / "triggers.json").write_text("{not valid json")
check("garbage triggers.json tolerated", _mgr5._load_triggers() == [])
check("absent triggers.json tolerated", mc.MissionManager(Path(_tmp.name) / "missions-trig3")._load_triggers() == [])

# --- daily "at" triggers, persisted state, per-trigger permissions ---------------

check("at parses valid time", mc._parse_at("07:30") == (7, 30))
check("at parses midnight", mc._parse_at("0:00") == (0, 0))
check("at rejects junk", all(mc._parse_at(v) is None for v in ("25:00", "7:60", "0730", 730, None, "a:b")))


async def _exercise_at_triggers():
    orig_ask = hermes_bridge.ask_hermes
    allow_seen: list[bool] = []

    async def fake_ask(text, sid):
        allow_seen.append(sid in hermes_bridge._tool_allowed_cookies)
        return "briefed"

    hermes_bridge.ask_hermes = fake_ask
    try:
        import time as _time
        tdir = Path(_tmp.name) / "missions-at"
        mgr = mc.MissionManager(tdir)
        (tdir / "triggers.json").write_text(json.dumps([
            {"title": "briefing", "prompt": "brief me", "at": "07:30", "permissions": "allow"},
        ]))
        base = _time.mktime((2026, 3, 10, 6, 0, 0, 0, 0, -1))  # a 06:00 local
        state: dict = {}
        mgr._scheduler_tick(state, now=base)                     # first sight: arm, no fire
        armed_quiet = len(mgr.missions) == 0
        mgr._scheduler_tick(state, now=base + 2 * 3600)          # 08:00 — 07:30 passed → fire
        fired = len(mgr.missions) == 1
        mgr._scheduler_tick(state, now=base + 3 * 3600)          # 09:00 — same day, no refire
        no_refire = len(mgr.missions) == 1
        # Server "down" across the next day's 07:30 — the 11:00 tick catches up.
        mgr._scheduler_tick(state, now=base + 86400 + 5 * 3600)
        caught_up = len(mgr.missions) == 2
        await asyncio.gather(*list(mgr._tasks))
        return mgr, armed_quiet, fired, no_refire, caught_up, allow_seen
    finally:
        hermes_bridge.ask_hermes = orig_ask


_mgr_at, _armed_quiet, _fired, _no_refire, _caught_up, _allow_seen = asyncio.run(_exercise_at_triggers())
check("at trigger arms on first sight", _armed_quiet)
check("at trigger fires once when its time passes", _fired and _no_refire)
check("at trigger catches up after downtime, once", _caught_up)
check(
    "trigger permissions allow tools during the mission only",
    _allow_seen == [True, True] and not hermes_bridge._tool_allowed_cookies,
    repr((_allow_seen, hermes_bridge._tool_allowed_cookies)),
)
check(
    "at mission records allow_tools",
    all(m.allow_tools for m in _mgr_at.missions.values()),
)

_state_mgr = mc.MissionManager(Path(_tmp.name) / "missions-state")
_state_mgr._save_trigger_state({"briefing": {"last_at": 123.0}})
check(
    "trigger state persists across managers",
    mc.MissionManager(Path(_tmp.name) / "missions-state")._load_trigger_state()
    == {"briefing": {"last_at": 123.0}},
)
(Path(_tmp.name) / "missions-state" / "trigger_state.json").write_text("{bad")
check("corrupt trigger state tolerated", _state_mgr._load_trigger_state() == {})

# --- queued trigger announcements -------------------------------------------------

main.active_websockets.clear()
main._pending_announcements.clear()
_trig_mission = mission_control.Mission(
    id="t-ann", title="briefing", cookie_id=mc.TRIGGER_COOKIE, session_id="s-ann",
    status="completed", result="Sky is clear, Dave.",
)
asyncio.run(main.on_mission_complete(_trig_mission))
check(
    "trigger report queues when the Bridge is empty",
    len(main._pending_announcements) == 1 and "Sky is clear" in main._pending_announcements[0],
    repr(main._pending_announcements),
)
for _i in range(main.MAX_PENDING_ANNOUNCEMENTS + 5):
    main._pending_announcements.append(f"note {_i}")
    del main._pending_announcements[:-main.MAX_PENDING_ANNOUNCEMENTS]
check("announcement queue is capped", len(main._pending_announcements) == main.MAX_PENDING_ANNOUNCEMENTS)
_drained = main._drain_announcements()
check(
    "announcements drain once",
    len(_drained) == main.MAX_PENDING_ANNOUNCEMENTS and main._drain_announcements() == [],
)

# --- mission prompt context ------------------------------------------------------

_mp = main._mission_prompt("fix the CI", [
    {"role": "user", "content": "the ci is red"},
    {"role": "assistant", "content": "I see it, Dave."},
])
check(
    "mission prompt carries conversation context",
    "Mission: fix the CI" in _mp and "Dave: the ci is red" in _mp and "HAL: I see it, Dave." in _mp,
)
check("mission prompt tolerates empty history", "(no prior conversation)" in main._mission_prompt("x", []))

# --- session event journal -------------------------------------------------------

main._log_session_event("evt-sess", {"type": "tool_call_update", "status": "completed", "title": "Read file"})
main._log_session_event("evt-sess", {"type": "tool_call", "status": "pending", "title": "transient — skip"})
main._log_session_event("evt-sess", {"type": "mission_update", "mission": {"id": "m", "title": "t", "status": "completed", "prompt": "HUGE" * 999}})
_evs = main.load_events("evt-sess")
check("journal keeps terminal events only", len(_evs) == 2 and _evs[0]["title"] == "Read file", repr(_evs))
check("journaled mission events are slimmed", "prompt" not in _evs[1]["mission"] and _evs[1]["mission"]["title"] == "t")
check("journal missing session is empty", main.load_events("nope-evt") == [])
check("journal has timestamps", all(e.get("ts") for e in _evs))

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

# Frontend must speak the backend's protocol: these strings are the contract.
for token in (
    'id="permbar"',            # permission Allow/Deny UI exists
    "/api/permission/",        # …and posts decisions to the endpoint
    "permission_request",      # …driven by the SSE event
    'id="missions-panel"',     # mission cards panel exists
    "/api/missions",           # …seeded from the missions endpoint
    "mission_session",         # tool events attributed to mission cards
    "set_mode",                # wake-word toggle frame
    "interim_transcript",      # live caption frames handled
    "no_wake_word",            # gated utterances stay silent
    "announce_ready",          # queued trigger reports delivered post-gesture
):
    check(f"frontend wires {token}", token in _frontend_src)
check(
    "ws.onopen re-sends wake mode after reconnect",
    "ws.onopen" in _frontend_src and "sendWakeMode" in _frontend_src,
)

# ----------------------------------------------------------------------------

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print("all tests passed")
