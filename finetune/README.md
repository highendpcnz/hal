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
| `estop_positive` | 160 | Urgent-phrasing → `emergency_stop`. Hand-authored (urgency doesn't template well); widened from an original 80 (12 unique phrases, heavily repeated) to 160 (~40 unique phrases) after a real fine-tune's eval showed this was the single weakest safety-relevant category (79.2% pass) — the exact failure was a confident "I've stopped everything" with no tool call, and the failing eval phrases turned out to be literal members of the old 12-phrase bank, just held out to eval. Low input diversity, not low volume, was the real gap. |
| `sensor_read_positive` | 80 | Read-only queries → `read_spatial_sensors`, with a reply that only states what the (synthetic) result actually returned. |
| `vision_positive` | 60 | "What do you see" phrasing → `capture_visual_scene`. Uses the 5-tool schema set (`vision_enabled=True`). |
| `out_of_bounds_decline` | 60 | Requests whose numbers exceed the declared JSON-schema bounds (e.g. "drive forward 200cm"). See design decision below. |
| `negative_conversation` | 140 | Plain chit-chat, identity questions, capability questions answered in prose — must **never** produce a tool call. This is the regression guard: a model that calls tools too eagerly is a new failure mode, not a fix. |
| `negative_hypothetical` | 100 | The hardest and most important negative class: action-shaped language that is hypothetical, past-tense, or a check-before-asking, e.g. "What would happen if you drove forward five centimeters?" Must produce prose, not a real tool call, and must not falsely claim a past action happened. Directly exercises the system prompt's "Never claim that an action succeeded unless a tool result confirms it." |
| `negative_missing_capability` | 60 | Requests for things this robot can't do at all (open a door, navigate to a room) → an honest capability refusal, not a mismatched tool call. |
| `relay_failure` | 90 | Tool call made, but the result is `{"ok": false, "error": ...}` (real error strings pulled from `CyberPiNotReadyError`/`SafetyError`/transport failures seen this session) → the reply must honestly report failure. This is the single most on-target category for the original bug: confidently claiming success that didn't happen. |
| `relay_success` | 90 | Tool call succeeds → reply accurately reflects it, isolated from the drive/turn categories above (turn + estop specifically). |
| `multi_turn_context` | 60 | Same core tool-call task, preceded by 1-3 turns of unrelated chit-chat, so reliability doesn't degrade as context grows (matches real session history usage). |

**Balance**: ~57% positive tool-calls (weighted toward drive/turn/estop,
the safety-critical ones), ~14% tool-result relay (explicitly split
success/failure), ~24% negatives (the guard against over-eager
tool-calling), ~5% out-of-bounds edge cases. 1300 total, 85/15 train/eval
split, stratified per category so eval isn't dominated by whichever
category happens to be largest.

**Confirmed live, first real fine-tune (before this estop widening)**:
777/777 training steps, final train loss 0.01595, val loss 0.01647.
Evaluated on the real held-out eval set with `reasoning=off` against the
actual production chat template: **88.6% overall (294/332 turns)**, up
from the base model's 0% under the same setting. Two clean failure
patterns, not noise: over-eager `read_spatial_sensors` calls (19/38
failures — the model reaching for a sensor read when uncertain, instead
of the right tool) and confident no-tool-call replies (13/38 — the
original bug, still present, concentrated in `estop_positive`). This
category's widening targets the second, more safety-critical pattern
directly; a real re-run and re-eval is needed to confirm it actually
helped rather than just adding volume.

**Dead end, confirmed live: the estop widening regressed, don't repeat
this shape of change.** Re-run and re-eval (retrained from this widened
1300-example set, same procedure) came back *worse*, not better: 83.8%
overall (300/358) and **64.6%** on `estop_positive` itself, down from
79.2%. The failure mode also spread to other tool-calling categories that
share the same `tool_call → tool_result → assistant reply` shape
(`sensor_read_positive` 62.5%, `vision_positive` 55.6%, `relay_success`
71.4%) — the model started outputting a confident confirmation ("Halted.",
"All motors stopped.", "I'm not tilted, Dave.") *instead of* the tool
call, i.e. a worse version of the exact bug this was meant to fix. The
widening bundled two changes: more unique trigger phrases (`ESTOP_PHRASES`
12→~40, the intended fix) and, new in this pass, a 6-way randomized
`ESTOP_REPLIES` pool for the post-tool-call confirmation text (previously
effectively fixed). The leading hypothesis is the reply diversification,
not the phrase widening — six replies randomly paired across ~40 phrases
may have taught the model that varied natural-language confirmation is
itself an acceptable output, bleeding into other categories with the same
structural shape. **Not yet isolated or retested**: a future attempt
should revert `ESTOP_REPLIES` to a single fixed string, keep the phrase
widening, and re-run in isolation before touching anything else. The
production model stayed on the pre-widening checkpoint (88.6%/79.2%
above); this widened dataset is committed (`451799e`) but was never
deployed.

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

## Files

- `generate_dataset.py` — builds `data/train.jsonl` / `data/eval.jsonl`.
- `chat_template.jinja` — the **exact** production chat template, extracted
  directly from the deployed GGUF's own `tokenizer.chat_template` metadata
  (via `gguf-py`'s `GGUFReader` against
  `~/models/gemma-4-e2b/gemma-4-E2B-it-Q4_0.gguf`), not a same-named
  Unsloth preset. `train_lora.py` loads this file directly rather than
  calling `unsloth.chat_templates.get_chat_template("gemma-4")` — trusting
  a preset to be byte-identical to this specific deployed model's template
  is exactly the kind of unverified assumption this project has learned
  not to make. Re-extract this file (see the header of `train_lora.py`
  for the `GGUFReader` snippet) if the deployed model ever changes.
- `train_lora.py` — the LoRA training script itself.
- `eval_harness.py` — scores a running llama-server checkpoint against
  `data/eval.jsonl`. See its own docstring.

## Running the training on Kaggle (free T4 tier)

**Confirmed live**: this has actually been run successfully end to end on
Kaggle. 777/777 steps across 3 epochs, final training loss 0.01595, final
validation loss 0.01647 (down from 0.0626 at step 50) — close train/val
loss, no sign of bad overfitting. Three real bugs turned up during that
run and are now fixed in `train_lora.py` itself (not just documented
here) — they're called out below at the exact step each one bit.

1. [kaggle.com/code](https://www.kaggle.com/code) → **New Notebook**.
2. Right sidebar → **Settings**:
   - **Accelerator** → GPU T4 x2 (the free tier's only T4 option — but see
     the single-GPU note below; this is about which session type to pick,
     not how many GPUs the process actually uses).
   - **Internet** → **On** (off by default; needed for `pip install` and
     to download the base model from Hugging Face — easy to miss and the
     run just fails on the first cell without it).
3. Right sidebar → **Add Input** → **Upload** → add `data/train.jsonl`,
   `data/eval.jsonl`, and `chat_template.jinja` as a new private Kaggle
   Dataset. **Verify the actual mount path before assuming it** — it was
   `/kaggle/input/datasets/<your-username>/<dataset-name>` on the real
   run, not the plain `/kaggle/input/<dataset-name>` a bare dataset
   attachment would suggest. Run `!ls /kaggle/input` (and `!find
   /kaggle/input -maxdepth 3` if that's ambiguous) in a cell first and use
   whatever it actually reports.
4. First cell:
   ```
   !pip install unsloth trl datasets
   %env HAL_FINETUNE_DIR=/kaggle/input/datasets/<your-username>/<dataset-name>
   %env HAL_FINETUNE_OUTPUT_DIR=/kaggle/working/output
   ```
   (`train_lora.py` reads both env vars — input is on the read-only
   `/kaggle/input/` mount, output has to go to the writable
   `/kaggle/working/` instead, or saving the trained adapter fails.)
5. Next cell: paste in `train_lora.py`'s contents and run it (or upload
   the script as a second input dataset and `!python train_lora.py`). The
   script itself now sets `CUDA_VISIBLE_DEVICES=0` and uses
   `use_gradient_checkpointing="unsloth"` — both fix a real, confirmed
   failure: Gemma 4 + Unsloth hit a cross-device tensor error on "GPU T4
   x2" sessions with both GPUs visible to one process, and a single T4's
   16GB needs the gradient-checkpointing headroom that two T4s' combined
   memory didn't. You shouldn't need to do anything extra for this one —
   it's handled in the script now, not a manual step.
6. Free tier gives roughly 30 GPU-hours/week, reset weekly — this run
   (777 steps on a single T4) took under an hour in practice.
7. When it finishes, download the **entire** `output/merged_fp16/`
   directory from the notebook's output panel, not just
   `model.safetensors` — `config.json`, `generation_config.json`,
   `tokenizer.json`, `tokenizer_config.json`, and `processor_config.json`
   are all required for the GGUF conversion step and are easy to miss if
   you only grab the (by far largest) weights file. Then continue with
   the GGUF conversion steps the script prints at the end.

## Running the training (general)

**This cannot run on a Mac or any machine without an NVIDIA GPU** —
Unsloth requires CUDA. Run `train_lora.py` on Kaggle's free T4 tier (the
one real published Gemma-4-E2B fine-tune found during research,
[helenk/gemma-4-E2B-finetune](https://huggingface.co/helenk/gemma-4-E2B-finetune),
was trained exactly that way), Colab, or a rented GPU. Upload
`data/train.jsonl`, `data/eval.jsonl`, and `chat_template.jinja` alongside
the script (or clone the repo), `pip install unsloth trl datasets`, then
`python3 train_lora.py`.

Model id (`unsloth/gemma-4-E2B-it`), LoRA config, and the
`get_chat_template`-vs-raw-template distinction above were confirmed
against Unsloth's own current Gemma 4 docs page as of this writing. LoRA
rank (`r=16, alpha=16`) matches the one real reference fine-tune at this
model size rather than the docs' lighter `r=8` quickstart default, given
this dataset's 12 categories are broader than a narrow single-skill tune.
`num_train_epochs=3` (~828 steps at effective batch size 4) is a real
multi-epoch pass sized for the ~1104-example train set, not the docs'
60-step smoke-test value. The `SFTConfig`/`SFTTrainer` field names
(`max_seq_length`, `dataset_text_field`, etc.) reflect `trl`'s API as
generally known, not independently re-verified against whatever exact
`trl`/`unsloth` versions are current when you actually run this — trl's
API does move between releases, so watch for deprecation warnings on
first run and adjust from there rather than assuming this script is
final.

**Real bug, also confirmed and fixed**: `apply_chat_template`'s tool-call
rendering needs `function.arguments` as an actual dict, not the JSON-
string form (`'{"distance_cm":20}'`) that `data/train.jsonl` stores and
that matches production's OpenAI-compatible wire format (what
`brain/gemma.py` actually sends/receives over HTTP, and what
`eval_harness.py`'s scoring expects back). Without conversion, every
tool-call example failed to render. `train_lora.py`'s
`_template_ready_messages()` now parses `arguments` from string to dict
right before calling `apply_chat_template`, on a deep copy — the stored
JSONL itself is untouched, so it still matches production and still works
with `eval_harness.py` unchanged.

After training, `train_lora.py` prints the exact next steps: download the
merged fp16 checkpoint, convert to GGUF and quantize to **Q4_0**
specifically with your local `llama.cpp` checkout (matching the exact
quantization already deployed — not Unsloth's own GGUF export helper's
default `q4_k_m`, which would introduce an unvalidated quantization
scheme on top of an already-narrow fine-tune), point
`HAL_GEMMA_MODEL_PATH` at it, start `llama-server` with `--reasoning off`,
and run `eval_harness.py` against it before trusting it on real hardware.
