# Pixel platform decision: Termux, not AVF Linux Terminal

Both the original proposal (`HAL_MBOT2_PROJECT_PROPOSAL.md`) and an earlier
exploratory session left this open, recommending "AVF Linux VM plus a small
Android host sensor gateway" without deciding. This resolves it: **Termux**.

## What this project actually needs from the OS layer

Direct, low-latency access to three real peripherals — CyberPi over
USB-serial, the Pixel's camera, and its microphone — from the same process
that runs the FastAPI bridge and the Gemma tool loop. Isolation from a
hostile host OS is not a requirement here; there is no untrusted code running
on this phone.

## Verified facts

1. **AVF and the Linux Terminal app are available on Pixel 7 Pro / Tensor
   G2.** Google Tensor (Pixel 6 and later) is verified to support AVF, and
   the Terminal app ships on Pixel 7 series and newer. This was not a given
   going in — Tensor G2 is the earlier chip in the supported range — but it
   checks out.
2. **AVF's Linux Terminal backend has no USB passthrough at all.** Not
   "difficult" — absent: "USB passthrough is not available on the AVF
   backend, which has no channel to hand the device to the VM." (QEMU-backed
   emulator VMs support USB passthrough; the actual on-device AVF backend
   Linux Terminal uses does not.) The only way to get CyberPi serial data
   into an AVF VM would be a second, separate Android app running on the
   host side of the VM boundary, relaying bytes across some IPC (network
   socket, vsock) — this is the "sensor gateway" the earlier session
   correctly flagged as necessary, and it is a whole extra app, not a
   config flag.
3. **Termux has an established (non-root, third-party) path for USB-serial.**
   `termux-usb` (via the Termux:API app) obtains a raw file descriptor for a
   USB device through Android's `UsbManager` permission dialog — Android
   does not expose `/dev/ttyUSB*`-style nodes to unprivileged apps at all,
   rooted or not, this is simply Android's model. Existing community bridge
   tools (`usbuart-termux`, and its ancestor `Termux-serial-tty`) turn that
   fd into a PTY that behaves like a normal serial device path. That matters
   concretely for this repo: `robot/cyberpi.py` / `telemetry.py` / `estop.py`
   / `motion.py` are all built on `pyserial` against a device path
   (`serial.Serial(port, BAUD_RATE, ...)`); if a PTY bridge is running, those
   likely need only a `--port /dev/pts/N`-style change, not a rewrite. This
   still needs a real spike against a real CH340 device (this CyberPi's chip,
   USB `1a86:7523`) before being treated as proven — the mechanism is
   documented, not yet exercised.
4. **Camera and mic access go through Termux:API** (`termux-camera-photo`,
   `termux-microphone-record`) — shell out, get a file back. That is the same
   "capture one frame, hand back bytes" shape `robot/camera.py` already uses
   against `ffmpeg` on the Mac; the contract ports, the backend swaps.
5. **llama.cpp compiles natively in Termux** with its own clang/cmake
   toolchain — a well-trodden community path, no VM memory overhead eating
   into the 12GB budget Gemma needs.
6. **The Phantom Process Killer risk (flagged High in the original proposal)
   is real and current, not overstated.** Android's background-process
   watchdog still kills Termux processes on Android 15+ even with
   `termux-wake-lock` held. It is mitigable, not blocking: a one-time
   Developer Options toggle ("Disable child process restrictions" on
   Android 14+) or an ADB `settings put global
   settings_enable_monitor_phantom_procs false` on Android 12–13. Treat this
   as a required device-setup step during bring-up, not a solved problem —
   verify it actually holds under this phone's specific Android build before
   relying on it for an unattended run.

## Why not AVF despite being available

It buys isolation this project doesn't need at the cost of the one thing it
needs most: direct hardware I/O. Every sensor — serial, camera, mic — would
need a bespoke host-side relay app under AVF; under Termux, each is a
documented (if sometimes third-party) API away, and the existing Mac-side
`robot/` code was written against exactly that shape (a path or an fd, not a
platform-specific driver). It also matches how this project has actually
worked so far: prove one primitive against real hardware before building the
next. AVF would force building and validating an entire second app before
ever reaching the CyberPi from the phone; Termux lets the phone follow the
same read-only-telemetry-first bring-up that already worked on the Mac.

## What remains unverified

Everything above is sourced, not assumed, but none of it has been exercised
on the actual Pixel yet. In particular:
- Whether `termux-usb` + a PTY bridge actually round-trips this CyberPi's
  CH340 adapter, and whether `pyserial` behaves identically against a PTY as
  it does against a real tty (the online-exec response timing this codebase
  already depends on).
- Whether the Phantom Process Killer mitigation actually holds on this
  phone's specific Android build under real load (STT + Gemma inference +
  serial I/O concurrently).
- Whether Termux's available Python build supports this repo's dependency
  set (`fastapi`, `uvicorn`, `pyserial`, `piper-tts`) without native-wheel
  pain — `faster-whisper`/`ctranslate2` was already flagged in the original
  proposal as the reason to move STT onto `sherpa-onnx` instead, which this
  decision does not revisit but does depend on.

The next step is connecting the phone and running that verification, the
same way USB enumeration and firmware identity were verified first for the
CyberPi rather than assumed — see `docs/robot-control-contract.md` for the
precedent.

## Sources

- [Android's Linux Terminal app is now widely available on Pixels](https://www.androidauthority.com/android-linux-terminal-app-available-3532999/) — device support (Pixel 7 series+, Tensor G2+)
- [Question - Questions about AVF feature (XDA)](https://xdaforums.com/t/questions-about-avf-feature.4760425/) — AVF backend has no USB passthrough channel; QEMU backend does
- [USB Serial · Issue #4618 · termux/termux-packages](https://github.com/termux/termux-packages/issues/4618) and [GitHub - thingsapart/usbuart-termux](https://github.com/thingsapart/usbuart-termux) — termux-usb fd-to-PTY bridge pattern
- [\[Bug\]: Termux processes are being killed by Android 15 background... · Issue #5150](https://github.com/termux/termux-app/issues/5150) — Phantom Process Killer still active on Android 15+ despite termux-wake-lock
