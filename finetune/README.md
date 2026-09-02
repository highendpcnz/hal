# Synthetic dataset for a Gemma 4 E2B LoRA fine-tune

## What this targets

Confirmed live (docs/termux-port-status.md, "Reasoning" section): with
`--reasoning off`, Gemma 4 E2B stops reliably calling tools on this app's
pipeline. It's not a training-data-quality issue in the abstract — it's
structural. The model's own chat template (extracted from the deployed
GGUF's `tokenizer.chat_template`) only opens a `<|think|>` scratchpad
channel when `enable_thinking` is true, positioned immediately after the
tool declarations in the system turn. With it off, generation starts cold
from `<|turn>model\n` with no scaffolding toward tool-call format, and the
model falls back to fluent, confident, action-sounding prose instead —
exactly the "Driving forward five centimeters..." reply that, live, moved
no wheels at all.

The goal of this dataset is narrow and specific: teach E2B to reliably
emit correct tool calls (or correctly *not* call one) in exactly this
`enable_thinking=false` setting, so `reasoning=off`'s real latency win
(confirmed ~23%, see docs/termux-port-status.md) can be recovered safely.
This is not a general personality/quality fine-tune.

## Why template-generated, not LLM-generated

Every target label here is computed from the same sampled values used to
build the phrasing (`generate_dataset.py`'s slot-filling functions), not
guessed by an LLM asked to produce plausible-looking examples. For a
safety-critical action space (bounded motor commands), a wrong label
baked into training data is worse than no data — an LLM-authored dataset
would need every single label hand-verified anyway, so generating
correct-by-construction labels directly is both cheaper and safer. The
tradeoff is less linguistic diversity than an LLM could produce; the
phrase banks below are deliberately varied within that constraint
(addressing prefixes, politeness suffixes, verb choice, disfluency-free
STT-plausible phrasing), but this is not a substitute for eventually
adding some hand-reviewed or human-sourced examples if the fine-tune's
eval shows it's overfitting to these specific templates.

## Format

Each line of `data/train.jsonl` / `data/eval.jsonl` is one JSON object:

```json
{
  "messages": [
    {"role": "system", "content": "<verbatim brain/GEMMA_SYSTEM.md>"},
    {"role": "user", "content": "HAL, drive forward 20 centimeters at 15 percent."},
    {"role": "assistant", "content": null, "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": "drive_straight", "arguments": "{\"distance_cm\":20,\"speed_pct\":15}"}}]},
    {"role": "tool", "tool_call_id": "call_0", "content": "{\"ok\":true}"},
    {"role": "assistant", "content": "Driven 20 centimeters."}
  ],
  "tools": [ /* the 4 or 5 tool schemas, copied verbatim from brain/gemma.py */ ],
  "tool_choice": "auto",
  "enable_thinking": false,
  "category": "drive_positive",
  "meta": {"distance_cm": 20, "speed_pct": 15, "speed_specified": true}
}
```

This mirrors exactly what `brain/gemma.py`'s `_complete()` sends to
llama-server (same message shape, same `tools`, same `tool_choice`), so
the training distribution matches the production request distribution.
`enable_thinking` is carried explicitly per-example rather than assumed,
so whatever training script consumes this (Unsloth's
`tokenizer.apply_chat_template(..., enable_thinking=False)` or equivalent)
renders it the same way `--reasoning off` does at inference. The system
prompt is read live from `brain/GEMMA_SYSTEM.md` at generation time, not
copy-pasted, so it can't silently drift from production.

## Categories and rationale

| category | count | purpose |
|---|---:|---|
| `drive_positive` | 220 | Core fix target: forward/backward phrasing → correct `drive_straight` call. Varies verb, address prefix, politeness, explicit vs. default speed, distance across the full ±50cm range. |
| `turn_positive` | 180 | Same, for `turn`. Half use signed-degree phrasing (unambiguous), half use left/right (see convention note below). |
| `estop_positive` | 80 | Urgent-phrasing → `emergency_stop`. Smaller bank, hand-authored (urgency doesn't template well). |
| `sensor_read_positive` | 80 | Read-only queries → `read_spatial_sensors`, with a reply that only states what the (synthetic) result actually returned. |
| `vision_positive` | 60 | "What do you see" phrasing → `capture_visual_scene`. Uses the 5-tool schema set (`vision_enabled=True`). |
| `out_of_bounds_decline` | 60 | Requests whose numbers exceed the declared JSON-schema bounds (e.g. "drive forward 200cm"). See design decision below. |
| `negative_conversation` | 140 | Plain chit-chat, identity questions, capability questions answered in prose — must **never** produce a tool call. This is the regression guard: a model that calls tools too eagerly is a new failure mode, not a fix. |
| `negative_hypothetical` | 100 | The hardest and most important negative class: action-shaped language that is hypothetical, past-tense, or a check-before-asking, e.g. "What would happen if you drove forward five centimeters?" Must produce prose, not a real tool call, and must not falsely claim a past action happened. Directly exercises the system prompt's "Never claim that an action succeeded unless a tool result confirms it." |
| `negative_missing_capability` | 60 | Requests for things this robot can't do at all (open a door, navigate to a room) → an honest capability refusal, not a mismatched tool call. |
| `relay_failure` | 90 | Tool call made, but the result is `{"ok": false, "error": ...}` (real error strings pulled from `CyberPiNotReadyError`/`SafetyError`/transport failures seen this session) → the reply must honestly report failure. This is the single most on-target category for the original bug: confidently claiming success that didn't happen. |
| `relay_success` | 90 | Tool call succeeds → reply accurately reflects it, isolated from the drive/turn categories above (turn + estop specifically). |
| `multi_turn_context` | 60 | Same core tool-call task, preceded by 1-3 turns of unrelated chit-chat, so reliability doesn't degrade as context grows (matches real session history usage). |

**Balance**: ~55% positive tool-calls (weighted toward drive/turn/estop,
the safety-critical ones currently broken), ~15% tool-result relay
(explicitly split success/failure), ~25% negatives (the guard against
over-eager tool-calling), ~5% out-of-bounds edge cases. 1220 total, 85/15
train/eval split, stratified per category so eval isn't dominated by
whichever category happens to be largest.

## One open design decision -- flagging rather than deciding silently

**Left/right turn convention: now hardware-confirmed.** A raw `turn(90, 8)`
called directly against real hardware (bypassing Gemma entirely, wheels
down on a clear floor to actually see the chassis reorient — a wheels-
raised test can't show this, see docs/robot-control-contract.md) pivoted
**right**. `generate_dataset.py` now sets `LEFT_IS_POSITIVE = False`
accordingly, and both `brain/gemma.py`'s and this file's `turn` tool
description were updated from the old vague "one way / the other" phrasing
to state it explicitly — a real production reliability improvement on its
own, independent of the fine-tune.

**Out-of-bounds requests: decline, not clamp-and-move.** When a
request's numbers exceed the tool's declared JSON-schema bounds (e.g.
"drive forward 200 centimeters" against a 50cm limit), this dataset trains
the model to verbally decline and explain the limit, rather than silently
substituting a different number than what Dave asked for and moving
anyway. That's a real design fork, not an obviously-correct default —
silently reinterpreting a safety-critical command's numbers is its own
trust problem, but a "decline everything out of range" policy is also a
usability cost for an honest overshoot ("drive forward 60cm" when the
limit is 50). If you'd rather it clamp-and-move-with-a-caveat, that's a
different `assistant_reply` in `gen_out_of_bounds()` and worth deciding
before training, not after.

## Regenerating

```sh
.venv/bin/python3 finetune/generate_dataset.py --seed 17
```

Deterministic given the seed. Category sizes are a dict at the bottom of
`generate_dataset.py` (`CATEGORY_GENERATORS`) — edit counts there to
rebalance.

## Not done yet

This is the dataset only — no training run, no eval harness, no merge/
quantize/deploy step. Per docs/termux-port-status.md and the earlier
research into this: Unsloth has an official Gemma 4 guide covering E2B
directly (LoRA, r=16 in the one real reference fine-tune found), and a
QLoRA run this size should be minutes on a single cloud GPU. The eval set
here (`data/eval.jsonl`) is held out and category-stratified for exactly
that step, but no eval harness exists yet to actually score a trained
checkpoint against it — that, plus the actual train/merge/GGUF-convert/
quantize/deploy pipeline, is the next chunk of work, not this one.
