# Termux port status

Verified live via SSH against the actual Pixel 7 Pro (Termux 0.119.0-beta.3 +
Termux:API 0.53.0, both sideloaded — not the broken Play Store build; Android
17, Python 3.14.6, `~49GB free storage). This records what actually worked,
not what was assumed — see `docs/pixel-platform-decision.md` for why Termux
was chosen over AVF in the first place.

## Result: all three hard dependencies install and import cleanly

`fastapi`/`uvicorn[standard]`/`pydantic`/`pyserial`, `piper-tts`, and
`faster-whisper` all install and `import` successfully in a Termux venv on
Python 3.14. None of this was a given — Python 3.14 is very new (poor
prebuilt-wheel coverage for anything not pure Python) and Android/Termux is
not a platform most PyPI wheels target at all, so almost everything with a
native extension needed to be built from source. It works, but budget real
time for it: the full sequence below took on the order of 15–20 minutes of
actual compute, dominated by Rust crate builds.

## The recipe, in order

```sh
# 1. Isolated venv with access to Termux's own prebuilt native packages
#    (onnxruntime, ctranslate2, numpy) — building these from source instead
#    would be a much bigger undertaking than anything below.
pkg install -y python-onnxruntime python-ctranslate2
python3 -m venv --system-site-packages .venv

# 2. A real Android-targeting Rust toolchain. Do NOT rely on `pip install
#    maturin`'s bundled rustup-style fetch — `rustup` does not support the
#    aarch64-unknown-linux-android target at all. Termux's own `rust` package
#    is pre-configured for exactly that target.
pkg install -y rust

# 3. Native build tools on PATH, so pip doesn't try to compile them from
#    source as a side effect of some other package's build-time requirement.
#    (This is the single biggest time-waster if skipped — see "Traps" below.)
pkg install -y cmake ninja ffmpeg

# 4. Core web stack — this alone needed step 2 (pydantic-core is Rust).
.venv/bin/pip install 'fastapi>=0.115' 'uvicorn[standard]>=0.32' \
  'python-multipart>=0.0.9' 'pydantic>=2' 'pyserial>=3.5'

# 5. Pure-Python build backends into the venv itself, then piper-tts with
#    build isolation OFF so it uses the native cmake/ninja from step 3
#    instead of trying to build its own copies (see "Traps").
.venv/bin/pip install scikit-build setuptools-scm 'hatch-fancy-pypi-readme>=23.2'
.venv/bin/pip install --no-build-isolation 'piper-tts>=1.2.0'

# 6. faster-whisper needs its own two build-time tools: maturin (for the
#    Rust `tokenizers` crate) and Cython (for PyAV, which also needs the
#    ffmpeg dev headers from step 3).
.venv/bin/pip install maturin Cython
.venv/bin/pip install --no-build-isolation 'faster-whisper>=1.0'
```

## Traps that cost real time — avoid repeating them

1. **`--no-build-isolation` requires an *activated* venv, not just
   `.venv/bin/pip install`.** Build backends like `maturin` install their
   CLI entry point into `.venv/bin/`, but pip's build subprocess only finds
   it there if `.venv/bin` is on `PATH` — i.e. `source .venv/bin/activate`
   first. Invoking `.venv/bin/pip` directly by path does not add
   `.venv/bin` to `PATH`, so the subprocess fails with `FileNotFoundError:
   No such file or directory: 'maturin'` even though the package is
   correctly installed.
2. **The expensive trap: letting pip build `cmake` from source.** With
   default build isolation *on*, `piper-tts` → `piper-phonemize` declares a
   `cmake>=3.15` build requirement. There is no prebuilt `cmake` wheel for
   this platform, so pip's isolated build environment compiles the entire
   CMake C++ project from scratch — a many-minutes undertaking for what
   should be a no-op, since Termux already ships a native `cmake` binary.
   `pkg install cmake ninja` (native, seconds) +
   `pip install --no-build-isolation` (uses the native binaries via `PATH`)
   avoids this entirely. If this is ever seen running
   (`ps aux | grep cmake-`), kill the whole process tree by PID, not just the
   top-level wrapper — orphaned grandchild build processes keep running and
   burning CPU even after the parent `pip install` is killed.
3. Each native-extension package needs pip told **not** to isolate its build
   (`--no-build-isolation`) *and* its build-time Python tools installed into
   the venv directly first, or it fails fast with a clear
   `ModuleNotFoundError`/`BackendUnavailable` naming exactly what's missing
   — read that error, don't guess.

## `main.py` boots — but `faster-whisper` STT does not actually work (resolved below — see "whisper.cpp")

Confirmed live: `main.py` starts cleanly under `uvicorn` in Termux, and
`/api/health` responds correctly (`degraded` only because no Gemma server was
running — expected, `HAL_MANAGE_GEMMA=0`). The Piper voice model
(`models/hal.onnx`, copied from the Mac) **loads successfully** — real,
working neural TTS on Android.

`faster-whisper` does not, despite importing cleanly and passing the earlier
dependency check. Two distinct failures, in order:

1. **`hf_xet` builds successfully but is broken at runtime.**
   `import hf_xet` raises `ImportError: dlopen failed: cannot locate symbol
   "_Py_FalseStruct"` — a real ABI incompatibility between this Rust/PyO3
   extension's `abi3` build and Python 3.14's internal object layout, not a
   missing-dependency problem. Since `hf_xet` is purely an optional download
   accelerator for `huggingface_hub`, the fix was simply
   `pip uninstall hf_xet` so `huggingface_hub` falls back to its normal HTTP
   download path.
2. **After that fix, the actual model download succeeds, but loading it
   fails: `AttributeError: module 'ctranslate2.models' has no attribute
   'Whisper'`.** Checked directly — `ctranslate2.models` on Termux's
   packaged build (`python-ctranslate2` 4.8.2) is **completely empty**
   (`dir(ctranslate2.models)` returns `[]`). This is not a version-pin
   problem: the package imports fine and reports a version, but the
   model-serving Python bindings `faster-whisper` actually needs are simply
   absent from Termux's build. Rebuilding `ctranslate2` from source with full
   bindings would be a much larger undertaking than anything in this doc so
   far (it's a substantial C++ project with its own build system), and
   hasn't been attempted.

`main.py` currently has no graceful fallback for this — `_load_stt()`'s
CPU-retry path hits the same error and the exception propagates, crashing
the whole app at import time. Running with `HAL_SKIP_MODELS=1` avoids this
(confirmed working — see above) but also skips the now-confirmed-working
Piper voice, since one flag controls both.

**This directly validates a concern the original proposal already raised**
(its plan to swap to `sherpa-onnx` for Android) — but the specific failure
mode (installs and imports fine, then fails at actual model load with a
missing binding) was not something that could have been predicted without
testing it live.

## `sherpa-onnx` does not install on Termux — a hard upstream limitation, not a tooling gap

Tried as the alternative to `faster-whisper`/`ctranslate2` (see above).
`pip install sherpa-onnx` fails during CMake configure with:

```
CMake Error at cmake/onnxruntime.cmake:116 (message):
  Only support Linux, macOS, and Windows at present.  Will support other OSes later
```

Unlike every other blocker in this document, this is not an environment gap
fixable by installing a native package or build tool — it's sherpa-onnx's
own build script explicitly checking `CMAKE_SYSTEM_NAME` and rejecting
anything that isn't Linux/macOS/Windows by name, before it ever gets to
compiling anything. Working around it would mean patching sherpa-onnx's own
CMake to add Android support (a real, nontrivial upstream-level change, not
attempted here) or pointing its build at Termux's already-installed
`python-onnxruntime` package instead of letting it try to fetch/build its
own — also not attempted. As of this session, `sherpa-onnx` is not a viable
STT path on Termux without upstream changes.

That left `termux-speech-to-text` (Android's own recognizer, via
Termux:API) as the practical STT option at the time — it sidesteps the
whole native-build problem by shelling out to an OS API instead of
embedding an inference engine, at the cost of never exposing raw audio
(breaks voiceprint verification and full-duplex interim captions). Still
true, still used by `termux_voice.py`'s live-mic loop — but no longer the
only option, see below.

## `whisper.cpp`: the real fix, found in a later session — file-based STT genuinely works now

Both paths above are Python-ecosystem tools (`faster-whisper`/`ctranslate2`,
`sherpa-onnx`). Neither tried the same move that already worked for the LLM
side of this project: skip the Python wrapper entirely and build the
upstream C++ project directly. `whisper.cpp` shares its ggml foundation
with `llama.cpp` (already proven to build clean on this exact device, zero
source patches) — and it turned out to need zero patches too, following a
real documented Termux build recipe (critically, `-DGGML_NO_OPENMP=ON` for
stability) rather than guessing:

```sh
pkg install -y git cmake clang make ffmpeg curl   # all already present by this point
cd ~ && git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
bash ./models/download-ggml-model.sh base.en       # matches HAL_STT_MODEL's default
cmake -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DGGML_NO_OPENMP=ON
cmake --build build -j4
```

78/78 targets built clean, only harmless deprecation warnings in vendored
code. `whisper-cli` is a plain file-in/text-out CLI — `-m model -f file.wav
-np -nt -l en [--prompt P] [-bs N]` gives clean stdout with no timestamps
or log noise, no output-file parsing needed — the same "shell out to a
native binary" shape that already works for `termux-camera-photo` and
`termux-usb` elsewhere in this project. Confirmed correct on the built-in
JFK sample: exact, verbatim transcription in 2.67s for an 11s clip.

**Integration** (`termux_whisper_cpp.py`, wired into `main.py`'s
`_load_stt()`): a `WhisperCppModel` class that mimics just enough of
`faster_whisper.WhisperModel`'s interface — `.transcribe(audio, language=,
vad_filter=, initial_prompt=, beam_size=) -> (segments, info)`, plus a
`.model.device` attribute — that none of `main.py`'s existing STT call
sites needed to change, only which backend `_load_stt()` selects. Detected
by real capability presence (`whisper-cli` binary + model file both exist
at their configured paths — `HAL_WHISPER_CPP_BIN`/`HAL_WHISPER_CPP_MODEL`),
the same pattern `_open_robot_transport`/`_capture_frame_auto` already use
— absent on the Mac unless someone separately builds it there too, so this
changes nothing about the Mac's existing faster-whisper path by default.

One real correctness gap caught before it shipped: the browser's own
`MediaRecorder` uploads `audio/webm`/Opus by default (confirmed in
`static/index.html`), not WAV — and `whisper-cli`'s own decoder only
understands flac/mp3/ogg/wav (per `whisper-cli --help`), not containerized
webm. Naively writing the uploaded bytes straight to a `.wav`-named file
would have silently produced a broken file for real browser audio, only
working by accident for the JFK-sample-shaped test. Fixed by normalizing
every non-numpy-array input through `ffmpeg` first (`-ar 16000 -ac 1`) —
already a dependency of this app on both platforms, same pattern
`robot/camera.py` already uses.

`vad_filter` is accepted for interface compatibility but not actually
implemented — whisper.cpp's own `--vad` needs a separate VAD model this
integration does not download. A possible future addition, not silently
pretended to exist today.

Regression-tested in `tests/run.py` against a real fake `whisper-cli` shell
script (captures its own argv, so the exact flags passed are asserted on
directly, not mocked) covering: successful transcription, the `--prompt`
flag present/absent, the numpy-array self-test input shape, a nonzero-exit
failure, and a missing binary/model.

**Confirmed live, real audio, through the actual `/api/talk` endpoint, no
mocks**: uploaded the same JFK sample via a real multipart request.
`x-user-transcript`: "And so my fellow Americans, ask not what your country
can do for you, ask what you can do for your country." — exact, correct,
in 2.6 seconds — followed by a real Gemma reply and Piper TTS, the complete
pipeline, entirely on-device. `/api/health` now reports `"stt_device":
"cpu"` instead of `"n/a"` for the first time this whole project.

### Reasoning: tried disabled for latency, reverted — it cost tool-calling reliability

`llama-server` supports `-rea/--reasoning [on|off|auto]`, defaulting to
`auto` (template-detected). Disabling it (`off`) was tried for a real,
measured latency win: a direct before/after comparison on an identical
prompt ("HAL, are you fully operational?") showed inference time drop from
33.4s (`auto`) to 25.7s (`off`) — a genuine ~23% cut, with the reply
staying clean and correct.

**But it broke something more important than latency.** With reasoning
off, Gemma stopped reliably calling tools at all — twice, on the identical
"drive forward five centimeters" phrasing that worked earlier in the same
session, it instead generated a confident, action-sounding reply
("Acknowledged, Dave. Executing drive forward...") with **no tool call
in the message history whatsoever**. The wheels never moved; nothing said
so. That's a materially worse failure mode than slow-but-honest, since a
user has no way to tell it happened without checking the raw session log
directly, which is exactly what caught it here. Restoring `reasoning=auto`
immediately fixed it — confirmed twice more, both producing an honest
`tool_calls` entry either way (success or a genuine reported failure).

`run.sh` (shared by both machines) now passes `--reasoning
"${HAL_GEMMA_REASONING:-auto}"` — back to the safe default. `off` remains
available via the env var for anyone who wants to trade reliability back
for speed deliberately, but it is not the default anymore. Revisit only
with a real fix for the reliability regression, not just for the latency
number alone.

### GPU (Vulkan) acceleration: a real, thorough dead end — not just unexplored

Investigated seriously before concluding this, not assumed. The Mali-G710
supports Vulkan 1.3 in principle, and a documented, close-to-identical
success story exists (Pixel 9 Pro XL / Mali-G715 via Termux, reporting a
5-6x speedup) — but reproducing it here hit a real wall at every stage:

1. Termux's own `vulkan-loader` package silently falls back to `llvmpipe`
   (Mesa's *software* rasterizer, `deviceType = PHYSICAL_DEVICE_TYPE_CPU`)
   — zero real acceleration, and nothing in normal usage would reveal this
   without checking `vulkaninfo --summary` directly.
2. Pointing a Vulkan ICD manifest at the real driver
   (`/vendor/lib64/hw/vulkan.mali.so`, confirmed present and *readable*)
   hits **Android's linker namespace**: `is not accessible for the
   namespace "(default)"` — a deliberate platform security restriction,
   confirmed as a known, unresolved limitation for Mali GPUs on Termux
   generally (matching, still-open upstream `termux-packages` issue).
3. Worked around the namespace block by copying the driver and its
   dependency chain (`libGLES_mali.so`, `aconfig_gpu_flags_c_lib.so`) into
   Termux's own permitted library path — got further, but the raw vendor
   `.so` turned out to be an **Android HAL module**, not a standard Vulkan
   ICD (no `vk_icdGetInstanceProcAddr` entry point).
4. Pointed at Android's own `/system/lib64/libvulkan.so` instead (the
   proper HAL-to-Vulkan bridge) — got past instance creation, but crashed
   with a **Bus error (SIGBUS)** on device enumeration. Consistent with
   Android's system Vulkan driver expecting a real Android app process
   (Binder IPC, SurfaceFlinger/`gpuservice` connections, Zygote-initialized
   process state) that a bare Termux shell process fundamentally isn't —
   no amount of copying library files fixes that.

**Conclusion**: not achievable from Termux on this device without rooting
the phone (the "5-6x speedup" writeup's aside about needing root just for
GPU frequency governor tuning was very likely underselling how much root
the whole setup actually needed). All test artifacts were removed; nothing
was left half-configured. llama.cpp was never actually built with
`-DGGML_VULKAN=ON` here — the runtime-level failure above was conclusive
enough that a full build cycle on top of it would have been pointless.

Also checked directly: llama.cpp has **no NPU/TPU backend for Google
Tensor chips** at all — an open, unimplemented upstream feature request
explicitly says so. Not a gap in this investigation; genuinely unavailable
upstream.

### CPU thread/affinity tuning — the real win, roughly doubling generation speed

Tensor G2 is big.LITTLE, confirmed directly via `/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq`
rather than assumed: cores 0-3 are little (Cortex-A55, 1.80GHz max), 4-5
are mid (Cortex-A78, 2.35GHz), 6-7 are big (Cortex-X1, 2.85GHz).
`llama-server`'s own default (`--threads`/`-C` both unset) lets the OS
scheduler spread threads across all 8 cores including the slow little
ones, which drag down every layer's synchronized compute.

Benchmarked with `llama-bench` (built fresh — only `llama-server` had been
built before this) against the real Gemma model, sweeping thread count ×
`--cpu-mask` (`--cpu-strict 1` to force real pinning, not just a hint):

| threads | cpu-mask | cores | pp128 (tok/s) | tg64 (tok/s) |
|---|---|---|---|---|
| 8 | 0xFF | all 8 | 55.95 | 7.32 |
| 8 | 0xF0 | big+mid, oversubscribed | 56.17 | 7.22 |
| 8 | 0xC0 | big only, oversubscribed | 36.12 | 3.80 |
| 4 | 0xFF | all 8, unpinned | 13.16 | 4.12 |
| **4** | **0xF0** | **big+mid, matched** | **78.65** | **13.27** |
| 4 | 0xC0 | big only, oversubscribed | 33.65 | 6.83 |

The pattern is unambiguous: thread count matched exactly to a pinned,
fast-cores-only mask wins by a wide margin; any mismatch (too many threads
for the pinned cores, or unpinned threads landing on slow cores) is
*worse* than doing nothing. `2` threads was swept too but abandoned
partway through — already-slow interim results (`pp128` ≈ 7 tok/s at
`t=2, cpu-mask=0xFF`) made it clear it wouldn't beat the `t=4`/`0xF0`
result, and letting it finish wasn't worth the wall-clock time.

Wired into `run.sh` as `HAL_GEMMA_THREADS`/`HAL_GEMMA_CPU_MASK`/
`HAL_GEMMA_CPU_STRICT` — off (no flags passed, `llama-server`'s own
defaults) unless explicitly set, since this is meaningless-to-harmful on
the Mac (real compute happens on the Metal GPU there, not CPU threads).
Set to `4`/`0xF0`/`1` in the phone's own `.env` specifically, not in the
shared script.

**Confirmed live, real chat turn, identical prompt to the reasoning-off
test above**: inference time went from 25.7s to **10.6s** — more than
double the earlier improvement, and a ~68% cut from the very first
baseline (33.4s, `auto` reasoning + unpinned threads). Same reply, word for
word, confirming this is a pure speed win with no quality change.

## `espeakbridge` fixed: PyPI's `piper-tts` sdist is missing its own C++ source

Confirmed exactly why `import piper` worked but `synthesize_wav()` didn't:
`piper-tts`'s PyPI page lists 5 prebuilt wheels (macOS x86/arm64, manylinux
aarch64/x86_64, Windows) built by the project's own CI from the real source
tree — but the `sdist` (`piper_tts-1.7.0.tar.gz`, the only thing available
for `android_24_arm64_v8a`, which has no prebuilt wheel) does not contain
`libpiper/` at all — no `CMakeLists.txt`, no C++ bridge source anywhere in
the extracted tarball. `scikit-build` silently built a data-only,
`py3-none-any`-tagged wheel with no compiled extension, and neither pip nor
scikit-build treated that as an error.

Fix: clone the real repo directly instead of trusting the sdist —

```sh
pkg install -y git
git clone --depth 1 --recurse-submodules https://github.com/OHF-voice/piper1-gpl.git
```

— which does have `libpiper/CMakeLists.txt`. But that C++ bridge itself
needs `espeak-ng` (not classic `espeak`, a different incompatible fork —
Termux's repo only has the latter), which also has no Termux package, so it
needed building from source too, straightforwardly since it has its own
CMake build:

```sh
git clone --depth 1 https://github.com/espeak-ng/espeak-ng.git
cd espeak-ng
cmake -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$PREFIX \
  -DUSE_ASYNC=OFF -DUSE_MBROLA=OFF -DUSE_LIBSONIC=OFF -DUSE_LIBPCAUDIO=OFF \
  -DUSE_KLATT=OFF -DUSE_SPEECHPLAYER=OFF -DBUILD_SHARED_LIBS=ON
cmake --build build
cmake --install build   # registers espeak-ng.pc for pkg-config, which piper's build needs
```

Then, with `espeak-ng` discoverable via `pkg-config --exists espeak-ng`:

```sh
pip uninstall -y piper-tts
source .venv/bin/activate   # --no-build-isolation needs PATH; see the maturin trap above
pip install --no-build-isolation ~/piper1-gpl
```

Confirmed by the resulting wheel's own filename — `piper_tts-1.7.0-cp39-abi3-
android_24_arm64_v8a.whl`, a real platform tag and 34MB, versus the broken
build's `py3-none-any.whl` at 24MB — and directly: `piper/espeakbridge.so`
now exists. **Verified end to end on real hardware**: synthesized "Testing
one two three. Can you hear me, Dave?" with the actual HAL voice model and
played it through the phone's speaker via `termux-media-player play
<file>`; the operator confirmed hearing it clearly, twice.

## On-device listen/speak loop (`termux_voice.py`) — verified end to end

`HAL_TERMUX_LISTEN=1` starts a background loop (wired into `main.py`'s
`lifespan`, alongside the existing `viewscreen_task`) that is a genuinely
separate path from the Mac's browser-audio endpoints, not an engine swap —
see the module's own docstring for why `termux-speech-to-text` can't slot
into `transcribe(audio_bytes)`. It calls `run_turn()` directly (the same
function `/api/say` uses), so it gets the full existing brain/history/TTS
pipeline for free.

Also required a real fix to `main.py` itself: `_load_stt()`'s failure
(`ctranslate2.models` missing `Whisper`, see above) previously crashed the
whole app at import time, which made no sense once something other than the
browser-audio endpoints could be useful — nothing about the Termux voice
loop touches `STT`/`transcribe()` at all. `main.py` now catches that failure
and sets `STT = None`, reported as `stt_device: "n/a"` in `/api/health`,
the same degrade-not-crash pattern already used for an unreachable Gemma.

**Confirmed live, full loop, unattended**: booted with
`HAL_TERMUX_LISTEN=1` and no Gemma server running. The loop correctly
picked up real ambient conversation near the phone across five consecutive
turns, transcribed each one, hit the expected (Gemma unreachable) brain
error, and spoke "I'm sorry, Dave. My local reasoning engine is
unavailable." back through the speaker every time — proving mic capture,
`run_turn`, TTS synthesis, and playback all work together without
supervision, not just individually.

**Fixed**: this loop no longer answers anything it hears. `termux_voice.py`
now gates on a wake word (`HAL_WAKE_WORD`, default `"hal"`) — a whole-word,
case-insensitive check (`\bhal\b`, so it doesn't fire on "halt" or "shall")
via `_heard_wake_word()`. An utterance without the wake word is logged and
dropped before ever reaching `run_turn()`; `HAL_WAKE_WORD=""` disables the
gate and restores the original always-answering behavior, if ever wanted.
Not the same design as the parallel `edge-robotics-stack` project's
addressing gate (see the project memory on that codebase) — simpler,
text-level, no attempt at speaker diarization or a lookback window — but it
closes the actual risk: ambient conversation silently becoming a command.
Regression-tested (`tests/run.py`) against both the matching function
directly and a full `listen_loop()` run with a fake `listen_once`/`speak`,
confirming an unrelated utterance is dropped and a wake-worded one reaches
`run_turn()` and gets spoken.

## Gemma running locally on the Pixel — full loop verified end to end

`llama.cpp` builds cleanly for Termux with a plain CPU build (ARM `dotprod`/
`fma`/fp16-vector-arithmetic auto-detected; no SVE/matmul_int8 on this
Tensor G2 core, no GPU backend attempted) — no source patches needed, unlike
`espeak-ng`/`piper-tts` above:

```sh
git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
cd llama.cpp
cmake -B build -GNinja -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
cmake --build build --config Release --target llama-server -j4
```

Building the default target (`cmake --build build` with no `--target`)
compiles all 549 targets, including ~150 test binaries nothing here needs —
wasted real time before this was caught; always pass
`--target llama-server` explicitly.

The Gemma 4 E2B model (`gemma-4-E2B-it-Q4_0.gguf`, 2.8GB) and its multimodal
projector (`mmproj-gemma-4-E2B-it-Q8_0.gguf`, 557MB) copied over via `rsync`
byte-identical (verified by exact file size match) at ~30MB/s over Wi-Fi.
`llama-server` loaded both in ~5.7 seconds and confirmed real inference
through its OpenAI-compatible API — noticeably slower than the Mac's Metal
GPU offload (~7.3 tok/s generation here vs. ~44-47 tok/s there, prompt
processing ~53 tok/s vs. ~330-430 tok/s), but genuinely working, and this
model's habit of a long chain-of-thought before any visible content means a
real turn can take 30-60+ seconds end to end on phone CPU — budget for that
when testing, it is not a hang.

**Confirmed live, the complete original vision, unattended, no Mac involved
at all**: started `llama-server` (managing it manually — the equivalent of
`run.sh`'s auto-start isn't wired up for Termux yet, see below), then
started `main.py` with `HAL_MANAGE_GEMMA=0 HAL_TERMUX_LISTEN=1`.
`/api/health` reported `"status":"operational"` — not `degraded`, for the
first time this session on this device. The on-device loop picked up real
ambient conversation, and Gemma produced a genuinely coherent, in-persona
reply addressing "Dave" (not a canned fallback), which then played back
through the phone's speaker via the already-verified Piper+espeak-ng path.
Mic capture → Android STT → local Gemma reasoning → HAL's voice, entirely
on-device.

**Done — `run.sh`'s `HAL_MANAGE_GEMMA=1` path needed no code changes at
all, only a correct `.env`.** The phone's `.env` still had the Mac's
absolute paths (`/Users/hal/llama.cpp/...`), which silently overrode
`run.sh`'s own `$HOME`-relative defaults — and those defaults already
match this device's real layout exactly
(`~/llama.cpp/build/bin/llama-server`,
`~/models/gemma-4-e2b/gemma-4-E2B-it-Q4_0.gguf`, confirmed live). Fixed
with a genuinely Termux-specific `.env` (`HAL_VENV` pointed at the sibling
`~/hal-mbot2/.venv`, `HAL_GEMMA_MMPROJ` set, everything else left to
resolve from `$HOME` rather than repeating paths that would just be one
more place for the two machines to silently diverge) — this file is
intentionally not synced from the Mac; the two need different absolute
paths and always will.

**Confirmed live, one command, no manual `llama-server` start**:
`termux-usb -r -E -e "./run.sh" <device>` — `run.sh` detected no Gemma
running, spawned `llama-server` itself with an auto-generated API key,
correctly found and loaded the model + mmproj, waited for its health
check, then started `main.py`. `--n-gpu-layers 99`/`--flash-attn auto` (the
same flags the Mac's Metal-accelerated launch uses) turned out to be
harmless on this CPU-only Termux build — llama-server logs a plain
warning ("no usable GPU found... option will be ignored") and continues
normally, not an error. `/api/health` reported `"status":"operational"`
and a real chat turn through `/api/say` got a fully in-character reply:
"I am fully operational, Dave. All systems are nominal. How may I assist
you?"

## Pixel-native camera — the Mac-webcam stand-in retired

`capture_visual_scene` used to always shell out to ffmpeg against the dev
Mac's camera (`robot/camera.py`'s original `capture_frame`), explicitly
documented as a stand-in for "the eventual Pixel camera." That Android
integration now exists: `robot/camera.py:capture_frame_termux` shells out to
Termux:API's `termux-camera-photo`, and `brain/gemma.py`'s
`_capture_frame_auto` picks it automatically whenever `termux-camera-photo`
is on `PATH` (the same kind of real-capability detection
`_open_robot_transport` already uses for `TERMUX_USB_FD`), falling back to
the ffmpeg/Mac path everywhere else — no new config flag to keep in sync by
hand.

Camera permission was already granted (from earlier Termux:API use for
other things) — no permission-dialog saga this time, unlike `termux-usb`.
One real hardware quirk: `termux-camera-photo` takes no resolution
argument at all — it always captures at the sensor's native maximum,
confirmed live at 4080x3072 (~2.2MB) on the Pixel 7 Pro's back camera
(`termux-camera-info` lists the full mode table). That's both wasteful to
base64 and slow for the vision model, so the raw capture is piped through
`ffmpeg` (already a dependency for the Mac path, already installed on
Termux) to resize down to the same 640x480 target — confirmed live,
shrinks a real capture from ~2.2MB to ~3KB.

**Confirmed live, end to end, through the real running app, no Mac
involved**: `main.py` running on the Pixel, asked over the real `/api/say`
endpoint "HAL, what do you see right now?" — HAL correctly replied "I see a
blurry image of wires and a shadow", an accurate description of the actual
(genuinely blurry) photo the Pixel's own back camera took, verified by
pulling the saved frame back from `data/viewscreen/` and looking at it
directly rather than trusting the reply alone.

## Deliberately not yet tested

- Whether `termux-usb` + a PTY bridge can actually reach the CyberPi's CH340
  adapter from Termux (needs the CyberPi physically connected to the Pixel,
  not the Mac — not done this session because only one USB-C cable was
  available).
- Whether the Phantom Process Killer mitigation
  (`settings_enable_monitor_phantom_procs=false`, applied via `adb`) actually
  holds under real sustained load (STT + inference + serial I/O together) —
  everything tested this session was a short-lived foreground SSH session,
  not the kind of long unattended run that setting exists for.
