---
title: HAL Voice MVP
emoji: 🔴
colorFrom: red
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
short_description: Push-to-talk conversation with HAL 9000's voice.
---

# HAL Voice MVP

Push-to-talk web app that lets you talk to HAL 9000. FastAPI backend wires Groq Whisper (STT) → Claude (LLM) → Piper TTS (HAL voice). Sessions persist to disk as JSON.

## Local setup

1. Install espeak-ng (macOS):
   ```
   brew install espeak-ng
   ```
2. Create a virtual env and install deps:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Download the HAL Piper voice:
   ```
   python download_model.py
   ```
4. Create `.env`:
   ```
   GROQ_API_KEY=gsk_...
   ANTHROPIC_API_KEY=sk-ant-...
   ```
5. (Optional) Edit `profile.md` with facts you want HAL to know about you.

## Run locally

```
.venv/bin/uvicorn main:app --reload --port 8000
```

Open http://localhost:8000, hold the red eye to talk, release to send.

## Deploy to Hugging Face Spaces

1. Create a new Space: https://huggingface.co/new-space
   - SDK: **Docker**
   - Hardware: **CPU Basic** (free)
   - Visibility: Public (free) or Private ($9/mo)
2. Add repository secrets (Settings → Variables and secrets):
   - `GROQ_API_KEY`
   - `ANTHROPIC_API_KEY`
3. Push this repo to the Space:
   ```
   git remote add space https://huggingface.co/spaces/<your-username>/<space-name>
   git push space main
   ```
4. The Space builds from the `Dockerfile`. First build takes ~5 min (downloads the HAL model). Subsequent builds are cached.
5. Open the Space URL, hold the eye, talk.

## Notes

- English only. The Piper HAL model is English-only by design.
- Sessions live in `data/sessions/` as JSON. On HF Spaces free tier, the filesystem is ephemeral — sessions reset on restart/rebuild. For persistence across restarts, upgrade to a Space with persistent storage ($5/mo) and set `HAL_DATA_DIR=/data`.
- Piper outputs 22050 Hz mono WAV, which browsers play natively.
- The HAL voice loads once at startup.
- Edit `profile.md` to give HAL static context about you; it gets prepended to the system prompt on startup.
