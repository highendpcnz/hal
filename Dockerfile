# VESTIGIAL — DOES NOT BUILD A WORKING IMAGE. Kept only for diffing against
# the original Hugging Face Space this repo was forked from. main.py now
# imports hermes_bridge.py and mission_control.py (not copied below) and
# expects the Hermes Agent CLI plus the HAL Piper voice on the host.
# See "Vestigial files" in README.md.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      espeak-ng \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 hal
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY download_model.py .
RUN python download_model.py

COPY hal_prompt.py main.py ./
COPY static/ ./static/

RUN mkdir -p /app/data && chown -R hal:hal /app
USER hal

ENV HAL_DATA_DIR=/app/data
EXPOSE 7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
