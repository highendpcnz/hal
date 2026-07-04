import asyncio
import os
import glob
import logging
from pathlib import Path
import argparse
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BRAIN_DIR = os.path.expanduser("~/.gemini/antigravity-cli/brain")
SKILLS_DIR = os.path.expanduser("~/.gemini/antigravity-cli/builtin/skills")
SANDBOX_DIR = os.path.join(os.path.dirname(__file__), "sandbox")

async def get_latest_transcript():
    """Finds the most recently modified transcript.jsonl file."""
    search_path = os.path.join(BRAIN_DIR, "**", ".system_generated", "logs", "transcript.jsonl")
    transcripts = glob.glob(search_path, recursive=True)
    if not transcripts:
        return None
    latest = max(transcripts, key=os.path.getmtime)
    return latest

async def analyze_transcript(client, transcript_path):
    """Uses OpenAI API to analyze the transcript."""
    logging.info(f"Analyzing transcript: {transcript_path}")
    with open(transcript_path, 'r') as f:
        lines = f.readlines()
        recent_lines = lines[-50:]
        transcript_content = "".join(recent_lines)

    prompt = f"""
    Analyze the following recent conversation transcript. 
    Identify any recurring errors, inefficient tool usage, or areas where a new specialized skill would help.
    If a new skill is needed, write the complete markdown content for a new skill file (SKILL.md) that addresses the issue.
    Include YAML frontmatter with `name` and `description`.
    Wrap the file content in a ```markdown codeblock.
    If no new skill is needed, simply output "NO_SKILL_NEEDED".

    Transcript:
    {transcript_content}
    """

    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are the Reflection Agent. Your job is to analyze agent transcripts and write new skills to improve future performance."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content

async def extract_skill(reflection_output):
    """Extracts markdown codeblock from reflection output."""
    if not reflection_output or "NO_SKILL_NEEDED" in reflection_output:
        return None, None
    
    start = reflection_output.find("```markdown")
    if start == -1:
        return None, None
    start += len("```markdown\n")
    end = reflection_output.find("```", start)
    if end == -1:
        return None, None
    
    skill_content = reflection_output[start:end].strip()
    
    name = "new_skill"
    for line in skill_content.split('\n'):
        if line.startswith("name: "):
            name = line.replace("name: ", "").strip()
            break
            
    return name, skill_content

async def test_skill_in_sandbox(client, name, content):
    """Uses OpenAI API to verify the skill in sandbox."""
    os.makedirs(SANDBOX_DIR, exist_ok=True)
    skill_dir = os.path.join(SANDBOX_DIR, name)
    os.makedirs(skill_dir, exist_ok=True)
    skill_path = os.path.join(skill_dir, "SKILL.md")
    
    with open(skill_path, "w") as f:
        f.write(content)
        
    logging.info(f"Skill saved to sandbox: {skill_path}")
    
    prompt = f"""
    Read the skill at {skill_path}.
    Simulate a task that would require this skill.
    Report SUCCESS if the skill instructions are clear and actionable.
    Report FAILURE if there are issues.
    
    Skill Content:
    {content}
    """
    
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are the Testing Agent. Your job is to validate new skills in the sandbox. You must output SUCCESS or FAILURE."},
            {"role": "user", "content": prompt}
        ]
    )
    
    output = response.choices[0].message.content
    logging.info(f"Testing Agent output: {output}")
    if "SUCCESS" in output:
        return True
    return False

async def promote_skill(name, content):
    """Promotes skill to active skills directory."""
    target_dir = os.path.join(SKILLS_DIR, name)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, "SKILL.md")
    
    with open(target_path, "w") as f:
        f.write(content)
        
    logging.info(f"Skill successfully promoted to: {target_path}")

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcript", type=str, help="Path to transcript to analyze")
    args = parser.parse_args()

    client = AsyncOpenAI()

    if args.transcript:
        latest_transcript = args.transcript
    else:
        latest_transcript = await get_latest_transcript()
        
    if not latest_transcript:
        logging.warning("No transcripts found.")
        return
        
    reflection = await analyze_transcript(client, latest_transcript)
    logging.info("Reflection complete.")
    
    name, skill_content = await extract_skill(reflection)
    if not name or not skill_content:
        logging.info("No new skill generated.")
        return
        
    logging.info(f"Generated new skill: {name}")
    
    success = await test_skill_in_sandbox(client, name, skill_content)
    if success:
        logging.info("Sandbox test passed. Promoting skill...")
        await promote_skill(name, skill_content)
    else:
        logging.error("Sandbox test failed. Skill discarded.")

if __name__ == "__main__":
    asyncio.run(main())
