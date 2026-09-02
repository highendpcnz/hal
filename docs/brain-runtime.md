# Local Brain Runtime

HAL owns the conversation boundary in `brain/`. The web server and mission
orchestrator call `brain.runtime`; neither imports Hermes. `HAL_BRAIN=gemma`
is the default and uses a local OpenAI-compatible chat endpoint supplied by
`llama-server` or Ollama. Set `HAL_BRAIN=hermes` only for compatibility testing.

## Local setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python download_model.py
export HAL_GEMMA_URL=http://127.0.0.1:8080/v1/chat/completions
export HAL_GEMMA_MODEL=gemma-4-e2b
./run.sh
```

When `HAL_GEMMA_URL` is not set, `run.sh` automatically looks for
`~/llama.cpp/build/bin/llama-server` and the Q4 model under
`~/models/gemma-4-e2b/`. It starts a loopback-only server with an ephemeral API
key, waits for readiness, writes `data/gemma-server.log`, and stops the server
with HAL. Set `HAL_MANAGE_GEMMA=0` for an externally managed endpoint. Override
`HAL_LLAMA_SERVER`, `HAL_GEMMA_MODEL_PATH`, context, GPU layers, or the optional
multimodal projector through the variables documented in `.env.example`.

Gemma conversation history is stored under `data/brain/gemma/`. Resetting a
browser session removes its provider history. The runtime does not import ACP,
search for Hermes executables, or read `~/.hermes` in Gemma mode.

## Tool safety

The first Gemma tool catalog contains only `read_spatial_sensors`. It opens the
CyberPi serial connection, obtains one validated getter-only snapshot, and
closes the port. Drive and rotation tools are intentionally absent until the
CyberPi watchdog and firmware-side proximity interlock have been implemented
and tested. The system prompt tells Gemma that an absent tool is unavailable.

## Tests

```sh
python3 tests/independent.py
```

This suite uses only the Python standard library. It verifies session locking,
event aliasing, local tool-call iteration, persistence, and the absence of
motion tools without importing the web/audio stack, Hermes, a model, or robot
hardware. `tests/run.py` remains the broader legacy frontend suite and selects
the Hermes adapter explicitly while that compatibility surface exists.
