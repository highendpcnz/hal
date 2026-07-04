#!/usr/bin/env python
"""Zero-dependency tests for the pure-python parts of the HAL frontend.

Run with the Hermes venv (no pytest required):

    ~/hermes-agent/.venv/bin/python tests/run.py

HAL_SKIP_MODELS=1 is set below, so the STT/TTS models never load — the whole
suite finishes in seconds and touches no audio, network, or inference.
"""
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

# ----------------------------------------------------------------------------

print()
if FAILURES:
    print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
    sys.exit(1)
print(f"all tests passed")
