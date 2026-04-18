# HAL Voice MVP

Push-to-talk web app that lets you talk to HAL 9000. FastAPI backend wires Groq Whisper (STT) → Claude (LLM) → Piper TTS (HAL voice). In-memory sessions, no auth, no database.

## Setup

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
4. Create `.env` (copy `.env.example`):
   ```
   GROQ_API_KEY=gsk_...
   ANTHROPIC_API_KEY=sk-ant-...
   ```

## Run

```
.venv/bin/uvicorn main:app --reload --port 8000
```

Open http://localhost:8000, hold the red eye to talk, release to send.

## Notes

- English only. The Piper HAL model is English-only by design.
- Sessions live in process memory; restarting the server wipes history.
- Piper outputs 22050 Hz mono WAV, which browsers play natively.
- The HAL voice is loaded once at startup — first request has no extra latency.
