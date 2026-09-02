"""LoRA fine-tune of Gemma 4 E2B on finetune/data/train.jsonl.

**Cannot run on this Mac.** Unsloth requires an NVIDIA GPU (CUDA); this
machine is Apple Silicon with no CUDA path. Run this on a CUDA box instead
-- Kaggle's free T4 tier is a real, proven option: the one published
Gemma-4-E2B fine-tune found during research for this project
(helenk/gemma-4-E2B-finetune on Hugging Face) was trained exactly that
way. Colab or a rented GPU (RunPod/Lambda/vast.ai) work the same way.

Setup on a fresh CUDA box:
    pip install unsloth trl datasets

Then, with this repo's finetune/ directory available (upload
finetune/data/train.jsonl, finetune/data/eval.jsonl, and
finetune/chat_template.jinja alongside this script, or clone the repo):

    python3 train_lora.py

## Why the chat template is loaded from a file, not Unsloth's presets

Unsloth ships named template presets ("gemma-4", "gemma-4-thinking") as a
convenience, but this fine-tune's entire purpose is exact parity with
what's actually deployed -- the template extracted directly from the
production GGUF's own `tokenizer.chat_template` metadata (see
finetune/README.md for how). Trusting a same-named preset to be
byte-identical to the specific deployed model's own template is exactly
the kind of unverified assumption this project has learned not to make
(see docs/termux-usb-bringup.md's whole online-mode-bootstrap saga for why).
`finetune/chat_template.jinja` is that exact extracted template; this
script loads it directly rather than calling
`unsloth.chat_templates.get_chat_template()`.

## Why enable_thinking=False, explicitly, per example

This is the whole point of the fine-tune (see finetune/README.md): the
production reliability gap only exists with `--reasoning off`, which
maps to `enable_thinking=false` in the template. Every example in the
dataset already carries `"enable_thinking": false` for this reason --
this script passes it through per-example rather than hardcoding it once,
so a future dataset revision that mixes thinking/non-thinking examples
(e.g. to also cover reasoning=auto) doesn't silently break.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

# Gemma 4 + Unsloth hit a cross-device tensor failure on Kaggle's "GPU T4
# x2" session (both GPUs visible to one process) -- confirmed live. Forcing
# a single visible GPU before any CUDA/torch import fixes it; must be set
# this early, not inside main(), since torch reads it at import time.
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")


def _script_dir() -> Path:
    # __file__ isn't defined when this file's content is pasted and run as
    # notebook cells (e.g. on Kaggle) rather than executed as a script --
    # only called as a fallback below, so it's fine if it's never invoked
    # in that context (the env vars are always set there instead).
    return Path(__file__).parent


# Overridable via HAL_FINETUNE_DIR so a Kaggle "Add Input" dataset upload
# (which lands under /kaggle/input/<dataset-name>/, not next to this
# script) doesn't need a manual file copy every run -- e.g. in a notebook
# cell: `import os; os.environ["HAL_FINETUNE_DIR"] = "/kaggle/input/hal-finetune-data"`
# before running this script, or `%env HAL_FINETUNE_DIR=/kaggle/input/hal-finetune-data`.
FINETUNE_DIR = Path(os.environ["HAL_FINETUNE_DIR"]) if "HAL_FINETUNE_DIR" in os.environ else _script_dir()
MODEL_NAME = "unsloth/gemma-4-E2B-it"
# NOT production's ctx-size (8192) -- that's inference-time context budget
# for a whole session, not what one training example needs. Measured
# directly: the longest rendered example in data/train.jsonl is ~920
# tokens (system prompt + tool declarations + a short exchange). Originally
# set to 1536; dropped to 1024 (confirmed live, still comfortably above the
# measured max) alongside the single-GPU fix above and gradient
# checkpointing below -- a single T4's 16GB is tighter than two T4s'
# combined memory, and this whole dataset is short-context regardless.
MAX_SEQ_LENGTH = 1024

# Real reference: helenk/gemma-4-E2B-finetune on Hugging Face used r=16,
# alpha=16 on this exact model size (LoRA on q,k,v,o,gate,up,down_proj).
# Unsloth's own docs default to r=8/alpha=8 as a lighter-weight baseline.
# Going with the proven-at-this-scale r=16 given this dataset's 12
# categories are more varied than a narrow single-skill fine-tune.
LORA_R = 16
LORA_ALPHA = 16
LORA_DROPOUT = 0

# ~1036 train examples; 3 epochs at effective batch size 4 (batch=1 x
# grad_accum=4) is roughly 780 steps -- a real multi-epoch pass, not the
# 60-step smoke-test value from Unsloth's own quickstart docs.
NUM_TRAIN_EPOCHS = 3
PER_DEVICE_TRAIN_BATCH_SIZE = 1
GRADIENT_ACCUMULATION_STEPS = 4
LEARNING_RATE = 2e-4

# Separate from FINETUNE_DIR deliberately -- on Kaggle, an "Add Input"
# dataset is mounted read-only under /kaggle/input/, so writing output
# there fails. Defaults to ./output next to this script (fine when running
# locally on a GPU box), but on Kaggle set HAL_FINETUNE_OUTPUT_DIR to
# something under /kaggle/working/ (the writable directory), e.g.
# `%env HAL_FINETUNE_OUTPUT_DIR=/kaggle/working/output`.
OUTPUT_DIR = (
    Path(os.environ["HAL_FINETUNE_OUTPUT_DIR"])
    if "HAL_FINETUNE_OUTPUT_DIR" in os.environ
    else _script_dir() / "output"
)
LORA_ADAPTER_DIR = OUTPUT_DIR / "lora_adapter"
MERGED_DIR = OUTPUT_DIR / "merged_fp16"


def load_jsonl(path: Path) -> list[dict]:
    examples = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def _template_ready_messages(messages: list[dict]) -> list[dict]:
    """`apply_chat_template`'s tool-call rendering expects
    `function.arguments` as an actual dict -- the JSON-string form
    (`'{"distance_cm":20}'`) is the OpenAI-compatible wire format
    `brain/gemma.py` sends/receives over HTTP and what the dataset stores
    to match production, but the template needs it parsed first. Confirmed
    live: without this, chat-template rendering fails on every tool-call
    example. Only affects rendering here -- the stored JSONL keeps the
    wire-format string, since that's what matches production and what
    eval_harness.py's scoring (json.loads on the predicted side) expects."""

    converted = copy.deepcopy(messages)
    for message in converted:
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        for call in message["tool_calls"]:
            function = call.get("function", {})
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = json.loads(arguments or "{}")
    return converted


def build_dataset(examples: list[dict], tokenizer):
    from datasets import Dataset

    texts = []
    for example in examples:
        text = tokenizer.apply_chat_template(
            _template_ready_messages(example["messages"]),
            tools=example["tools"],
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=example["enable_thinking"],
        )
        texts.append(text)
    return Dataset.from_dict({"text": texts})


def main() -> None:
    from unsloth import FastLanguageModel
    from trl import SFTConfig, SFTTrainer

    train_path = FINETUNE_DIR / "data" / "train.jsonl"
    eval_path = FINETUNE_DIR / "data" / "eval.jsonl"
    template_path = FINETUNE_DIR / "chat_template.jinja"
    for path in (train_path, eval_path, template_path):
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found -- upload finetune/data/*.jsonl and "
                "finetune/chat_template.jinja alongside this script"
            )

    print(f"loading {MODEL_NAME} (4-bit, max_seq_length={MAX_SEQ_LENGTH})...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_NAME,
        dtype=None,
        max_seq_length=MAX_SEQ_LENGTH,
        load_in_4bit=True,
    )

    # Exact production template, not an Unsloth preset -- see module docstring.
    tokenizer.chat_template = template_path.read_text()

    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        lora_alpha=LORA_ALPHA,
        lora_dropout=LORA_DROPOUT,
        bias="none",
        target_modules="all-linear",
        random_state=3407,
        # Confirmed live alongside the single-GPU fix above -- a single
        # T4's 16GB needs this headroom at this batch size/seq length that
        # two T4s' combined memory didn't. Unsloth's own optimized value,
        # not plain True/False.
        use_gradient_checkpointing="unsloth",
    )

    train_examples = load_jsonl(train_path)
    eval_examples = load_jsonl(eval_path)
    print(f"train: {len(train_examples)} examples, eval: {len(eval_examples)} examples")

    train_dataset = build_dataset(train_examples, tokenizer)
    eval_dataset = build_dataset(eval_examples, tokenizer)

    args = SFTConfig(
        output_dir=str(OUTPUT_DIR / "checkpoints"),
        per_device_train_batch_size=PER_DEVICE_TRAIN_BATCH_SIZE,
        gradient_accumulation_steps=GRADIENT_ACCUMULATION_STEPS,
        num_train_epochs=NUM_TRAIN_EPOCHS,
        warmup_steps=10,
        learning_rate=LEARNING_RATE,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=50,
        optim="adamw_8bit",
        weight_decay=0.001,
        lr_scheduler_type="linear",
        seed=3407,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LENGTH,
        packing=False,  # each example is a full turn structure; packing would corrupt tool_call framing
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        args=args,
    )

    print("training...")
    trainer.train()

    print(f"saving LoRA adapter to {LORA_ADAPTER_DIR}")
    model.save_pretrained(str(LORA_ADAPTER_DIR))
    tokenizer.save_pretrained(str(LORA_ADAPTER_DIR))

    print(f"merging to fp16 and saving to {MERGED_DIR}")
    model.save_pretrained_merged(str(MERGED_DIR), tokenizer, save_method="merged_16bit")

    print(
        "\nDone. Next steps (see finetune/README.md):\n"
        f"  1. Download {MERGED_DIR} from this environment.\n"
        "  2. Convert to GGUF and quantize to Q4_0 with your local llama.cpp checkout,\n"
        "     matching the exact quantization already deployed:\n"
        "       python3 convert_hf_to_gguf.py <merged_dir> --outfile gemma-4-e2b-tuned-f16.gguf\n"
        "       ./build/bin/llama-quantize gemma-4-e2b-tuned-f16.gguf gemma-4-e2b-tuned-Q4_0.gguf Q4_0\n"
        "  3. Point HAL_GEMMA_MODEL_PATH at the new GGUF and start llama-server with\n"
        "     --reasoning off.\n"
        "  4. Run finetune/eval_harness.py against it and compare to the reasoning=auto\n"
        "     baseline before trusting it on real hardware.\n"
    )


if __name__ == "__main__":
    main()
