"""Reflection loop: analyze recent agent transcripts and draft new skills.

Safety model
------------
This script can write files that change future agent behavior, so promotion
is gated:

  * The default run is a DRY RUN — a candidate skill is written to the local
    sandbox/ directory and reported, but nothing is installed.
  * --promote installs the skill, after showing its full content and asking
    for confirmation on the terminal.
  * --yes skips the confirmation (for automation you explicitly trust).

The "sandbox review" step is an advisory LLM opinion about whether the skill
text is clear and actionable. It does not execute anything; the human
confirmation above is the real gate.

Configuration (env vars, all optional)
--------------------------------------
  HAL_REFLECTION_MODEL       chat model (default: gpt-4o)
  HAL_REFLECTION_BASE_URL    OpenAI-compatible endpoint, e.g. a local
                             llama.cpp / vLLM / Ollama server — keeps the
                             loop fully local like the rest of this project
  HAL_REFLECTION_BRAIN_DIR   transcript search root
                             (default: ~/.gemini/antigravity-cli/brain)
  HAL_REFLECTION_SKILLS_DIR  where promoted skills are installed
                             (default: ~/.gemini/antigravity-cli/builtin/skills)

Note: this loop currently targets the Antigravity CLI's transcripts and
skills, not Hermes Agent. It lives in this repo as a documented experiment;
point the two *_DIR variables elsewhere to retarget it.
"""
import argparse
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_MODEL = os.environ.get("HAL_REFLECTION_MODEL", "gpt-4o")
DEFAULT_BASE_URL = os.environ.get("HAL_REFLECTION_BASE_URL", "").strip() or None
BRAIN_DIR = Path(
    os.path.expanduser(
        os.environ.get("HAL_REFLECTION_BRAIN_DIR", "~/.gemini/antigravity-cli/brain")
    )
)
SKILLS_DIR = Path(
    os.path.expanduser(
        os.environ.get(
            "HAL_REFLECTION_SKILLS_DIR", "~/.gemini/antigravity-cli/builtin/skills"
        )
    )
)
SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"

TRANSCRIPT_TAIL_LINES = 50

# Skill names come from LLM output that was itself derived from transcript
# content — treat them as untrusted. The name becomes a directory name, so
# anything but a plain slug (no separators, no dots) is rejected.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SKILL_BLOCK_RE = re.compile(r"```markdown\s*\n(.*?)```", re.S)
_NAME_LINE_RE = re.compile(r"^name:\s*(.+)$", re.M)


def find_latest_transcript() -> Path | None:
    """Most recently modified transcript.jsonl under the brain directory."""
    transcripts = list(BRAIN_DIR.glob("**/.system_generated/logs/transcript.jsonl"))
    if not transcripts:
        return None
    return max(transcripts, key=lambda p: p.stat().st_mtime)


async def analyze_transcript(client: AsyncOpenAI, model: str, transcript_path: Path) -> str:
    """Ask the Reflection Agent whether the transcript suggests a new skill."""
    logging.info("Analyzing transcript: %s", transcript_path)
    lines = transcript_path.read_text().splitlines(keepends=True)
    transcript_content = "".join(lines[-TRANSCRIPT_TAIL_LINES:])

    prompt = f"""
    Analyze the following recent conversation transcript.
    Identify any recurring errors, inefficient tool usage, or areas where a new specialized skill would help.
    If a new skill is needed, write the complete markdown content for a new skill file (SKILL.md) that addresses the issue.
    Include YAML frontmatter with `name` and `description`. The name must be a short lowercase slug (letters, digits, hyphens).
    Wrap the file content in a ```markdown codeblock.
    If no new skill is needed, simply output "NO_SKILL_NEEDED".

    Transcript:
    {transcript_content}
    """

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are the Reflection Agent. Your job is to analyze agent transcripts and write new skills to improve future performance.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def _slugify(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def extract_skill(reflection_output: str) -> tuple[str, str] | None:
    """Pull (name, content) out of the reflection output, or None.

    Returns None both when no skill was proposed and when the proposed name
    fails slug validation — a non-slug name is how a poisoned transcript
    would try to steer the install path, so it disqualifies the skill.
    """
    if not reflection_output or "NO_SKILL_NEEDED" in reflection_output:
        return None
    match = _SKILL_BLOCK_RE.search(reflection_output)
    if match is None:
        return None
    skill_content = match.group(1).strip()

    name_match = _NAME_LINE_RE.search(skill_content)
    name = _slugify(name_match.group(1)) if name_match else "new_skill"
    if not _SKILL_NAME_RE.fullmatch(name):
        logging.error("Rejecting skill with unsafe name %r.", name)
        return None
    return name, skill_content


def save_to_sandbox(name: str, content: str) -> Path:
    skill_path = SANDBOX_DIR / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content)
    return skill_path


async def review_skill(client: AsyncOpenAI, model: str, name: str, content: str) -> bool:
    """Advisory LLM review of the sandboxed skill.

    This asks a model whether the skill text looks clear and actionable — it
    executes nothing, so a pass is an opinion, not a test result. Promotion
    still requires the human confirmation gate.
    """
    prompt = f"""
    Review the following candidate skill named {name!r}.
    Simulate a task that would require this skill.
    Report SUCCESS if the skill instructions are clear and actionable.
    Report FAILURE if there are issues.

    Skill Content:
    {content}
    """

    response = await client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are the Testing Agent. Your job is to review new skills in the sandbox. You must output SUCCESS or FAILURE.",
            },
            {"role": "user", "content": prompt},
        ],
    )
    output = response.choices[0].message.content or ""
    logging.info("Review output: %s", output)
    # An explicit FAILURE wins over an incidental "SUCCESS" in the prose.
    if "FAILURE" in output:
        return False
    return "SUCCESS" in output


def confirm_promotion(name: str, content: str, target: Path, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        logging.error(
            "No terminal to confirm on; refusing to promote. Rerun with --yes if you trust this run."
        )
        return False
    print(f"\n----- candidate skill: {name} -----")
    print(content)
    print("----- end of skill -----")
    answer = input(f"Install this skill to {target}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def promote_skill(name: str, content: str) -> Path:
    target = SKILLS_DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze an agent transcript and draft a new skill. "
        "Dry run by default; --promote installs after confirmation."
    )
    parser.add_argument("--transcript", type=Path, help="path to a transcript to analyze")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="install the skill after review and confirmation (default: dry run)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="with --promote: skip the interactive confirmation",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="chat model to use")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="OpenAI-compatible endpoint (e.g. a local server) instead of api.openai.com",
    )
    args = parser.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if args.base_url and not api_key:
        api_key = "not-needed"  # local OpenAI-compatible servers ignore the key
    client = AsyncOpenAI(base_url=args.base_url or None, api_key=api_key)

    transcript = args.transcript or find_latest_transcript()
    if transcript is None:
        logging.warning("No transcripts found under %s", BRAIN_DIR)
        return 1
    if not transcript.exists():
        logging.error("Transcript not found: %s", transcript)
        return 1

    reflection = await analyze_transcript(client, args.model, transcript)
    logging.info("Reflection complete.")

    extracted = extract_skill(reflection)
    if extracted is None:
        logging.info("No new skill generated.")
        return 0
    name, content = extracted

    sandbox_path = save_to_sandbox(name, content)
    logging.info("Candidate skill %r saved to sandbox: %s", name, sandbox_path)

    if not await review_skill(client, args.model, name, content):
        logging.error("Review flagged the skill; leaving it in the sandbox.")
        return 1

    if not args.promote:
        logging.info(
            "Dry run (default): nothing installed. Review %s and rerun with --promote to install.",
            sandbox_path,
        )
        return 0

    target = SKILLS_DIR / name / "SKILL.md"
    if not confirm_promotion(name, content, target, assume_yes=args.yes):
        logging.info("Promotion declined; candidate remains in the sandbox.")
        return 0

    installed = promote_skill(name, content)
    logging.info("Skill installed: %s", installed)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
