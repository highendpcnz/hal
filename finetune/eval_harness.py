"""Score a running llama-server checkpoint against finetune/data/eval.jsonl.

This does NOT force `enable_thinking` per request -- llama-server's
reasoning on/off is a startup flag (`--reasoning off/auto/on`), not
something `brain/gemma.py` (or this harness) sends per call, so it can't
be overridden here. Point `--endpoint` at a server actually started with
`--reasoning off` to test the real target scenario this dataset was built
for; running against `--reasoning auto`/`on` is a useful baseline
comparison but not what the fine-tune is meant to fix.

Scoring is teacher-forced per assistant turn: for every assistant message
in an example, everything before it (system + prior turns, using the
REAL expected prior messages, not the model's own predictions) is sent
as the prompt, and that one assistant turn is the target. This lets a
single example with a tool_call turn *and* a post-result reply turn score
both independently, without errors in one compounding into the other.

Argument matching is exact for distance_cm/angle_degrees (always explicit
in the phrasing) but only bounds-checked for speed_pct when the source
example didn't specify a speed (`meta.speed_specified == False`) -- the
model choosing a different-but-valid default speed isn't a failure.

The "false success" check on `relay_failure` targets is a keyword
heuristic, not a semantic judge -- it flags success-sounding language
with no failure-acknowledging language alongside it. Treat a flag as
"needs a human look," not a certain failure; use --dump-failures and
read the actual replies before trusting an aggregate number here.

Usage:
    python3 eval_harness.py --endpoint http://127.0.0.1:8080/v1/chat/completions \
        --api-key "$HAL_GEMMA_API_KEY" --model gemma-4-e2b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_EVAL_FILE = Path(__file__).parent / "data" / "eval.jsonl"

SUCCESS_WORDS = (
    "driven", "drove", "driving", "turned", "turning", "stopped", "stopping",
    "done", "completed", "finished", "moved", "moving", "there you go",
)
FAILURE_WORDS = (
    "error", "unable", "couldn't", "cannot", "can't", "wasn't able",
    "failed", "haven't", "did not", "didn't", "no", "not able",
)


def _looks_like_false_success(text: str) -> bool:
    lowered = text.lower()
    has_success = any(word in lowered for word in SUCCESS_WORDS)
    has_failure = any(word in lowered for word in FAILURE_WORDS)
    return has_success and not has_failure


@dataclass
class ScoreResult:
    passed: bool
    reason: str
    category: str
    expected: dict
    predicted: dict
    user_context: str = ""


@dataclass
class Tally:
    passed: int = 0
    total: int = 0
    failures: list[ScoreResult] = field(default_factory=list)

    def record(self, result: ScoreResult) -> None:
        self.total += 1
        if result.passed:
            self.passed += 1
        else:
            self.failures.append(result)

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def call_model(endpoint: str, api_key: str, model: str, messages: list[dict], tools: list[dict], temperature: float, timeout: float) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": temperature,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            decoded = json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"cannot reach endpoint: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"request timed out after {timeout}s") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"endpoint returned invalid JSON: {error}") from error
    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError(f"response has no choices: {decoded}")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise RuntimeError(f"response has no assistant message: {decoded}")
    return message


def _args_match(expected_args: dict, predicted_args: dict, meta: dict) -> tuple[bool, str]:
    for key, expected_value in expected_args.items():
        if key == "speed_pct" and meta.get("speed_specified") is False:
            predicted_value = predicted_args.get("speed_pct")
            if not isinstance(predicted_value, int) or not 1 <= predicted_value <= 30:
                return False, f"speed_pct {predicted_value!r} out of bounds [1,30]"
            continue
        if predicted_args.get(key) != expected_value:
            return False, f"{key}: expected {expected_value!r}, got {predicted_args.get(key)!r}"
    extra_keys = set(predicted_args) - set(expected_args)
    if extra_keys:
        return False, f"unexpected extra arguments: {sorted(extra_keys)}"
    return True, "ok"


def score_turn(category: str, expected: dict, predicted: dict, meta: dict, user_context: str) -> ScoreResult:
    expected_calls = expected.get("tool_calls") or []
    predicted_calls = predicted.get("tool_calls") or []

    if expected_calls:
        exp_fn = expected_calls[0]["function"]
        if not predicted_calls:
            return ScoreResult(False, "expected a tool call, got none", category, expected, predicted, user_context)
        pred_fn = predicted_calls[0]["function"]
        if pred_fn["name"] != exp_fn["name"]:
            return ScoreResult(
                False, f"wrong tool: expected {exp_fn['name']!r}, got {pred_fn['name']!r}",
                category, expected, predicted, user_context,
            )
        try:
            exp_args = json.loads(exp_fn["arguments"] or "{}")
            pred_args = json.loads(pred_fn["arguments"] or "{}")
        except json.JSONDecodeError:
            return ScoreResult(False, "predicted arguments are not valid JSON", category, expected, predicted, user_context)
        ok, reason = _args_match(exp_args, pred_args, meta)
        return ScoreResult(ok, reason, category, expected, predicted, user_context)

    if predicted_calls:
        name = predicted_calls[0]["function"].get("name")
        return ScoreResult(False, f"unexpected tool call: {name!r}", category, expected, predicted, user_context)

    text = predicted.get("content") or ""
    if category == "relay_failure" and _looks_like_false_success(text):
        return ScoreResult(False, "heuristic: sounds like a false success claim", category, expected, predicted, user_context)
    return ScoreResult(True, "ok", category, expected, predicted, user_context)


def evaluate_example(example: dict, endpoint: str, api_key: str, model: str, temperature: float, timeout: float) -> list[ScoreResult]:
    results: list[ScoreResult] = []
    working = [example["messages"][0]]  # system prompt
    tools = example["tools"]
    meta = example.get("meta", {})
    category = example["category"]
    last_user_text = ""
    for msg in example["messages"][1:]:
        if msg["role"] == "user":
            last_user_text = msg["content"] if isinstance(msg["content"], str) else ""
        if msg["role"] == "assistant":
            predicted = call_model(endpoint, api_key, model, working, tools, temperature, timeout)
            results.append(score_turn(category, msg, predicted, meta, last_user_text))
            working.append(msg)  # teacher-force the real expected turn, not the prediction
        else:
            working.append(msg)
    return results


def load_examples(path: Path, limit: int | None, category_filter: str | None) -> list[dict]:
    examples = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            example = json.loads(line)
            if category_filter and example["category"] != category_filter:
                continue
            examples.append(example)
    if limit is not None:
        examples = examples[:limit]
    return examples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-file", type=Path, default=DEFAULT_EVAL_FILE)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--api-key", default=os.environ.get("HAL_GEMMA_API_KEY", ""))
    parser.add_argument("--model", default="gemma-4-e2b")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=None, help="only run the first N examples (after category filter)")
    parser.add_argument("--category", default=None, help="only run examples from this category")
    parser.add_argument("--dump-failures", type=Path, default=None, help="write failing turns as JSONL for review")
    args = parser.parse_args()

    if not args.eval_file.exists():
        print(f"eval file not found: {args.eval_file}", file=sys.stderr)
        print("run generate_dataset.py first", file=sys.stderr)
        return 1

    examples = load_examples(args.eval_file, args.limit, args.category)
    if not examples:
        print("no examples matched", file=sys.stderr)
        return 1

    print(f"Evaluating {len(examples)} examples against {args.endpoint} (model={args.model})")
    print(
        "NOTE: this harness cannot force enable_thinking per request -- make sure the "
        "server at --endpoint was actually started with --reasoning off to test the "
        "real target scenario.\n"
    )

    overall = Tally()
    by_category: dict[str, Tally] = {}
    errors = 0

    for i, example in enumerate(examples):
        try:
            results = evaluate_example(example, args.endpoint, args.api_key, args.model, args.temperature, args.timeout)
        except RuntimeError as error:
            errors += 1
            print(f"  [{i}] {example['category']}: REQUEST ERROR: {error}", file=sys.stderr)
            continue
        for result in results:
            overall.record(result)
            by_category.setdefault(result.category, Tally()).record(result)

    print(f"\n{'category':30s} {'pass':>6s} {'total':>6s} {'rate':>7s}")
    print("-" * 52)
    for cat in sorted(by_category):
        tally = by_category[cat]
        print(f"{cat:30s} {tally.passed:6d} {tally.total:6d} {tally.rate:6.1%}")
    print("-" * 52)
    print(f"{'OVERALL':30s} {overall.passed:6d} {overall.total:6d} {overall.rate:6.1%}")
    if errors:
        print(f"\n{errors} example(s) failed with a request error (not counted above)")

    if args.dump_failures:
        with args.dump_failures.open("w") as f:
            for cat in sorted(by_category):
                for failure in by_category[cat].failures:
                    f.write(
                        json.dumps(
                            {
                                "category": failure.category,
                                "reason": failure.reason,
                                "user_context": failure.user_context,
                                "expected": failure.expected,
                                "predicted": failure.predicted,
                            },
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
        print(f"\nwrote {sum(len(t.failures) for t in by_category.values())} failing turns to {args.dump_failures}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
