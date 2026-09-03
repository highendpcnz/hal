# Cloud inference for the brain: what it would take, and what it would cost

Exploratory, not a decision. Records what was actually checked in the code and
measured on the device, so the tradeoff can be argued from facts rather than
impressions. A cloud-LLM Termux robot stack already exists separately on this
phone and was deliberately not this project's direction; this revisits that.

## The headline: the swap is nearly configuration-only

`brain/gemma.py` is not "a llama.cpp client". It is a generic
OpenAI-compatible chat client that happens to point at localhost:

- endpoint is `HAL_GEMMA_URL` (default `http://127.0.0.1:8080/v1/chat/completions`)
- auth is a standard `Authorization: Bearer <HAL_GEMMA_API_KEY>` header
- model id is `HAL_GEMMA_MODEL`
- tools are sent as OpenAI `tools` + `tool_choice`
- vision attaches an `image_url` content part carrying a `data:image/jpeg;base64,...`
  URL — the OpenAI vision shape

`brain/runtime.py` selects a provider from `HAL_BRAIN` against a small
`BrainProvider` protocol (`ask`, `health`, `cancel`, …), so a second provider is
a drop-in, not a refactor.

Pointing the existing provider at a cloud endpoint is therefore roughly:

```sh
HAL_MANAGE_GEMMA=0                      # stop run.sh spawning local llama-server
HAL_GEMMA_URL=https://api.anthropic.com/v1/chat/completions
HAL_GEMMA_API_KEY=...                   # never commit; .env only
HAL_GEMMA_MODEL=<model id>
```

Verified compatibility layers (both are OpenAI-shaped `/chat/completions`):

- **Anthropic** — `https://api.anthropic.com/v1/` with the OpenAI SDK; native
  API is `/v1/messages`, the OpenAI shape is a compatibility layer.
- **Google Gemini** — `https://generativelanguage.googleapis.com/v1beta/openai/`.
  Note it is *not* fully spec-compliant (usage returned in every chunk; rejects
  some parameters).

## Three things that genuinely break, and are not config

1. **The health check derives its own URL.** `gemma.py` strips the
   `/v1/chat/completions` suffix and GETs `{base}/v1/models`. Gemini's base is
   `/v1beta/openai/`, so the derived URL is wrong and the check fails — HAL then
   reports `degraded` while inference actually works. Needs an explicit
   `HAL_GEMMA_HEALTH_URL` or a provider-aware probe.
2. **Vision is gated on a local file.** `vision_enabled` is
   `bool(HAL_GEMMA_MMPROJ)` — the presence of a local multimodal projector. A
   cloud model needs vision enabled without any mmproj on disk, so that gate has
   to become explicit rather than inferred.
3. **The whole `reasoning=off` apparatus stops meaning anything.** No chat
   template, no `enable_thinking`, no `--reasoning` flag. Which also means:

## The fine-tune becomes unnecessary — say it plainly

The entire fine-tune programme exists to make a 2B-class local model call tools
reliably with thinking disabled. A frontier hosted model does that out of the
box. Going cloud-first retires that work as the primary path. It does not make
it worthless — it is the offline fallback, and it is the only thing that runs
when there is no network — but it should not be defended for its own sake.

## Latency: measured, and less one-sided than expected

Real turns on the Pixel with the v4 fine-tune (`/api/latency`, Tensor G2, 8
cores, CPU-only, Q4_0):

| turn | inference | total |
|---|---:|---:|
| short conversational reply | 650 ms | 927 ms |
| short conversational reply | 702 ms | 881 ms |
| sensor question (one tool round trip) | 4 470 ms | 5 225 ms |
| "Stop!" (tool call + reply) | 9 499 ms | 10 230 ms |

So the honest picture is **not** "local is slow". Conversational turns are
already sub-second and would likely get *slower* over a network round trip.
The win is concentrated in tool-calling turns, where two sequential model calls
currently cost 4.5–10 s and a hosted model would plausibly do the same in 1–3 s.

## The blocker worth taking seriously: emergency stop over a network

Measured on v4, on-device, "Stop!" mid-conversation fires the tool 4/5 — good,
not certain. Moving that path to the cloud trades one failure mode for another:
model reliability improves, but a stop command now depends on WiFi, DNS, TLS and
a provider's availability, while the robot keeps moving. That is a worse failure
mode than an unreliable local call, because it fails *silently and externally*.

**Recommendation regardless of which brain wins:** intercept stop words locally,
before any model call. A deterministic keyword match on the transcript
("stop", "halt", "emergency stop") that invokes `emergency_stop` directly is
sub-100 ms, needs no model, and works with the network down. This removes the
safety-critical path from the LLM's reliability budget entirely — which is worth
doing *today*, on the local build, independent of any cloud decision.

## The other costs

- **Privacy.** HAL's mic loop is ambient and has already picked up real
  conversation in this house (see `docs/termux-port-status.md`). Cloud inference
  streams that off-device to a third party. This is a materially different
  posture, and is the strongest argument for keeping local as the default.
- **Offline.** A robot in a garage or workshop with poor WiFi is exactly where
  this thing is meant to work.
- **Cost.** Per-turn token billing against a currently-free local model,
  including every ambient false trigger.
- **Secrets.** `CLAUDE.md` already forbids committing cloud keys; a cloud path
  means a real key living in the phone's `.env`, on a device that travels.

## The shape that probably wins

Not either/or:

1. **Local keyword intercept for `emergency_stop`** — do this now, either way.
2. **Keep local Gemma as the default brain** — privacy, offline, sub-second chat.
3. **Add a `cloud` provider behind `HAL_BRAIN`**, reusing the existing
   OpenAI-compatible client, for when quality matters more than privacy
   (complex multi-step requests, vision description) or while debugging whether
   a failure is the model's fault or the harness's.

Item 3 is genuinely cheap given the architecture. Item 1 is the one with real
safety value and does not depend on the rest.
