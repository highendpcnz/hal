# Termux → CyberPi USB access: bring-up record

Getting `termux-usb` to grant access to the CyberPi's USB-serial adapter from
the Pixel took an extended live debugging session with two genuinely
separate root causes, only one of which was actually about USB permissions.
Both are recorded here so neither needs rediscovering.

## Prerequisite: wireless `adb`

Working on this needed `adb` reachable while the Pixel's one USB-C port was
committed to the CyberPi (via an OTG adapter) rather than the Mac — a port
can be a USB host (serving the CyberPi) or a USB device (visible to the Mac),
never both at once. `ssh` over Wi-Fi (already set up, see
`docs/termux-port-status.md`) covers routine work, but diagnosing this
specific problem needed `logcat`, which needs `adb`.

**Getting Wireless debugging working at all was its own multi-hour detour.**
`adb pair`/`adb connect` failed instantly and unconditionally with `No route
to host`, and along the way this ruled out, in order: stale
`~/.android/adb_known_hosts.pb` (cleared, no effect), the macOS Application
Firewall (confirmed off), `pf` static rules and dynamic state (both clean —
`sudo pfctl -s rules` / `-s states`), the macOS Local Network privacy
permission (already granted), a stale/conflicting local `adb` server process
(genuine bug in our own process management — `kill %1` doesn't survive
across separate tool invocations; had to `kill -9` the actual PID), installed
System/Network Extensions (`systemextensionsctl list` → zero), configuration
profiles (`profiles list` → none), proxy environment variables (none), and a
stale ARP entry (entry was fine).

**The real cause**: `tcpdump` on `en0` during a connection attempt captured
*zero packets* — proving the failure was entirely local, before anything hit
the wire. Differential testing (`adb` connecting to loopback: works; to the
Mac's own LAN IP via `en0`: works; to the phone specifically: fails; `nc` to
the phone: works) narrowed it to something `adb`-specific and
phone-IP-specific. The phone is the one host on the network actively
advertising `_adb-tls-pairing._tcp.local`/`_adb-tls-connect._tcp.local` mDNS
services — and `adb`'s own Rust mDNS subsystem was independently and
consistently failing: `Failed to send query to zero socket Err(Os { code: 65,
kind: HostUnreachable, message: "No route to host" })`, because this Mac's
IPv6 default routes were all pointed at orphaned `utun` tunnel interfaces
(no owning process found; likely leftover from long-removed VPN software)
that don't carry multicast, and `en0` itself had **no multicast route at
all**. `adb`'s connect path for an mDNS-relevant IP apparently shares that
broken multicast machinery internally, even for a plain direct-IP connect —
explaining why only the phone's IP was affected. Fix:

```sh
sudo route add -net 224.0.0.0/4 -interface en0
```

Immediately after, both `adb pair` and the older `adb tcpip 5555` +
`adb connect <ip>:5555` method started working. (Removing the broken IPv6
default routes via the `utun` interfaces, tried earlier as a narrower fix,
had no effect on its own — the missing multicast route was the actual
issue.)

## The actual `termux-usb` permission problem

With `adb`/`logcat` finally available, the permission dialog's "flashes and
immediately closes" behavior turned out to be **real, correct Android
behavior responding to a real hardware event**, not a software or security
bug at all. `logcat` during a live attempt showed, within the same
millisecond as the dialog closing:

```
UsbHostManager: Removed device at /dev/bus/usb/00X/00X was already gone
AOC: usb.cc: ... conn state=0
AOC: notifyUSBConnection ... disconnect
VRI[UsbPermissionActivity]: visibilityChanged oldVisibility=true newVisibility=false
```

The CyberPi was **actually disconnecting from the USB bus** right as Termux
tried to claim it, and Android correctly dismissed a permission dialog for a
device that had just vanished. Every earlier theory tested against this
symptom — dialog timing, foreground/background focus, Android's
"restricted settings" protection for sideloaded apps (genuinely broken and
worth having fixed regardless — see below) — was chasing the same visible
symptom without addressing the actual cause underneath it.

**Fix**: power the mBot2 from its own battery instead of relying on the
Pixel's OTG bus power. The CyberPi board likely draws more current at the
moment of being claimed than the phone can reliably source in USB-host mode,
causing a brief brownout/reset on the port. Once self-powered, the same
`termux-usb -r <device>` request granted cleanly and the grant persisted
across repeated checks.

## Real side-fixes made along the way, worth keeping regardless

- **Android "Restricted settings"** was denying `com.termux` (and, more
  ambiguously, `com.termux.api`) the ability to show certain permission
  dialogs at all — a real Android 13+ protection for sideloaded apps.
  Confirmed via `adb shell cmd appops get com.termux | grep RESTRICTED`
  (was `deny`). Fixed via Settings → Apps → Termux → ⋮ → "Allow restricted
  settings" (and the same for Termux:API). This did not turn out to be the
  cause of the dialog-closing symptom, but it's a real restriction worth
  having lifted regardless, and may matter for other Termux:API permission
  flows later.
- **`adb pair`/`adb connect` working at all** (the multicast route fix
  above) is independently useful for any future session that needs `adb`
  while the USB port is committed elsewhere.

## The fd → `pyserial`-equivalent bridge: done, and hardware-verified

With `termux-usb` permission working (above), built `robot/android_usb.py`:
a userspace CH340 driver operating directly on the granted fd via
`ctypes` + `libusb`, implementing the same `SerialTransport` protocol
`telemetry.py`/`estop.py`/`motion.py` already depend on — so none of that
already-verified code needed to change.

Two things made this work that are worth recording precisely:

1. **Plain `libusb_init()` fails on Android** with `LIBUSB_ERROR_IO` (it
   tries full bus enumeration, which the sandbox blocks). Fix:
   `libusb_init_context()` with `LIBUSB_OPTION_NO_DEVICE_DISCOVERY` (value
   `2`, confirmed from the installed `libusb.h`, not guessed), then
   `libusb_wrap_sys_device(ctx, fd, &handle)` to wrap the fd `termux-usb`
   already granted, skipping libusb's normal (blocked) device-open path
   entirely.
2. **The CH340 register-write sequence** (`_configure_ch340` in
   `android_usb.py`) is transcribed directly from the Linux kernel's
   `drivers/usb/serial/ch341.c` (`ch341_configure()` + `ch341_open()`), not
   reverse-engineered — fetched the actual source rather than working from
   a summary. The baud-rate divisor algorithm was verified against the
   driver source *and* cross-checked against an independent reference,
   producing identical register values (`a=0xcc03, b=0x0008` for 115200).

**Confirmed live, in stages**: first, the raw `libusb_wrap_sys_device` +
`libusb_control_transfer` path alone, reading the real device descriptor —
`idVendor=0x1a86 idProduct=0x7523`, an exact match for the CH340 confirmed
on the Mac side. Then the full CH340 init sequence completed with no
errors. Then — the real test — the already-verified
`robot.telemetry.CyberPiTelemetryClient` was constructed directly on top of
`Ch340UsbTransport` and its mode-query round-trip produced a real,
correctly-parsed response from the board (`CyberPi is in upload mode`) —
proving genuine two-way `f3f4` protocol communication through this entirely
new code path, not just a successful USB claim.

## New open question, not a bug: getting to *online* mode without mBlock

The mode query above correctly reported **upload mode**, not **online
mode** — expected, since "online mode" was previously entered via mBlock's
Live session while the CyberPi was connected to the Mac, and this is a
completely fresh connection through the Pixel. The codebase only has
`encode_current_mode_query_frame()` (read-only) — there is no verified way
to *command* a transition into online mode; `ONLINE_MODE_MARKER` was
recorded as something observed the board *report*, not confirmed safe to
send *to* it. Sending an unverified frame to the firmware is exactly the
category of risk this whole bring-up has been deliberately conservative
about (see the crash hazard the parallel `edge-robotics-stack` project hit
sending unframed data — a different mistake, same class of risk).

This matters beyond convenience: only one host can be connected to the
CyberPi at a time, so "use mBlock on the Mac once, then hand off to the
Pixel" isn't physically possible for an already-established session — mBlock
would need to run *before* every Pixel session, which undermines the goal of
a Pixel-hosted robot that doesn't need the Mac at deployment time.

**Partially resolved, then corrected — this took two passes, and the first
one was wrong for half the codebase.** Rather than guess at a mode-switch
frame, first tested whether the *existing*, already-verified read-only
`cyberpi.get_battery()` online-exec query (the same one `telemetry.py`
already uses) works regardless of the reported mode. It does:
`CyberPiOnlineResponse(sequence=1, result=90, error=None)`, live, with the
mode query reporting upload the whole time.

That result was over-generalized. `telemetry.py`, `estop.py`, and
`motion.py` were all changed the same way: `initialize()` logs the mode
instead of raising `CyberPiNotReadyError` when it isn't online. This is
correct for `telemetry.py` — reads genuinely are mode-independent, confirmed
live: the unmodified `CyberPiTelemetryClient` runs its full `initialize()` +
`read_snapshot()` through `Ch340UsbTransport`, real telemetry (battery 90%,
motors stationary) over the Pixel, independent of any Mac/mBlock session
ever having touched the board first.

It is **not** correct for `estop.py`/`motion.py`. Sending a real
`drive_straight(5, 20)` from the Pixel while the board reported upload mode
(see "Wired into HAL's own process" below) produced a clean, error-free
online-exec response — and zero physical movement, confirmed twice, with
the operator watching the second time specifically to check. Encoder
telemetry read immediately before and after was bit-for-bit identical
across both attempts. Getters read a register regardless of mode; a real
actuation call like `cyberpi.mbot2.straight()` apparently needs genuine
online mode to actually reach the motor driver, and the firmware
acknowledges the script either way with no error to distinguish the two
cases. For `estop.py` specifically — the one client whose entire purpose is
stopping the robot — a silent, error-free no-op is the one failure mode
that cannot be tolerated.

**Reverted** `estop.py` and `motion.py` back to raising
`CyberPiNotReadyError` when mode isn't online; `telemetry.py` was left as
the log-only version, since that half of the original finding held up.
Three regression tests were rewritten to match: estop/motion now assert
`CyberPiNotReadyError` is raised under a mode-reports-upload fake serial;
telemetry's original "proceeds anyway" test is unchanged.

Consequence: real motor control from the Pixel does not currently work at
all, not "works with a caveat" — the board boots into upload mode, and the
only previously-known way into online mode is a Mac/mBlock Live session
first, which defeats the point of a Pixel-hosted robot. See "Still open"
below.

## Wired into HAL's own process

`brain/gemma.py`'s `GemmaProvider._open_telemetry_client()` now branches on
the `TERMUX_USB_FD` env var — the exact one `termux-usb -E` sets — rather
than always assuming a pyserial device path. When it's set (non-empty), it
builds `robot.telemetry.CyberPiTelemetryClient` directly on top of
`Ch340UsbTransport(int(fd))`; otherwise it falls back to
`CyberPiTelemetryClient.open(HAL_ROBOT_PORT)` exactly as before. No change
was needed in `robot/telemetry.py` itself — the constructor already accepted
any `SerialTransport`-shaped object, only the `.open(path)` convenience
classmethod was pyserial-specific.

The real deployment invocation this enables (not yet run end-to-end, only
unit-tested against a faked `Ch340UsbTransport` in `tests/run.py`):

```sh
termux-usb -r -E -e "python3 main.py" /dev/bus/usb/001/002
```

`-E` hands the fd over as `TERMUX_USB_FD` instead of an argv position, which
matters here since `main.py`'s own argv isn't meant to carry it.

Only the telemetry read path (`read_spatial_sensors`) is wired this way.
`estop.py`/`motion.py` are not constructed anywhere in `main.py`/`brain/`
yet — see below.

**Confirmed live, end to end, no mocks**: not `main.py` itself yet (that
also boots the web server, TTS/STT, and the voice loop — more than this
specific wiring needed to prove out), but the exact same unmodified
`GemmaProvider._read_spatial_sensors()` main.py's own tool loop calls,
imported and invoked for real via

```sh
termux-usb -r -E -e "python3 gemma_telemetry_live.py" /dev/bus/usb/001/002
```

with the script doing nothing but `GemmaProvider(EventHub())._read_spatial_sensors()`.
Result: `{'ok': True, 'bring_up': {'mode': UPLOAD, 'firmware_version':
'44.01.016', ...}, 'telemetry': {'battery_percent': 100.0, ...}}` — the
`TERMUX_USB_FD` branch picked up the fd `termux-usb -E` set, built
`Ch340UsbTransport` on it, and ran a real bring-up + snapshot through
`CyberPiTelemetryClient`, independent of any Mac session. Device stayed
enumerated afterward.

## Resolved: entering genuine online mode without mBlock, at all

The blocker above — real motor control not working from a Pixel-only
session — is now fixed. The fix came from USB packet capture of a real
mBlock session, not from guessing at the `makeblock` write API this
project had deliberately avoided (see `docs/robot-control-contract.md`'s
"Decision: investigation closed here").

**Method**: with SIP disabled (`csrutil disable`, Recovery Mode — needed
specifically because Wireshark's USB capture on macOS Catalina+ requires
it; re-enable afterward with `csrutil enable`) and the CyberPi reconnected
to the Mac, `tshark -i XHC0 -i XHC1` captured the actual bytes mBlock's own
"Enter Live" flow and a real block execution send over USB. Two captures
were needed — the first (just clicking "Enter Live" and waiting) turned out
incomplete; only a capture of the user actually running a block from
mBlock's editor showed the real, complete sequence.

**The real sequence**, hardware-verified end to end (a real
`cyberpi.mbot2.straight()` actually moved the wheels afterward, confirmed
twice): three online-exec scripts, sent in order, each using wait-flag
`0x04` — not `ONLINE_WAIT`/`ONLINE_NO_WAIT` (sending `online_restart` with
the standard `ONLINE_WAIT` flag instead gets back a `SyntaxError` from the
device, confirmed live):

```python
try:
    import config
except:
    pass

try:
    config.write_config("repl_enable", False)
except:
    pass

try:
    online_restart
except:
    pass
```

`online_restart` alone is **not** sufficient — this was tested and failed
three separate ways before the second capture revealed the missing
`config.write_config("repl_enable", False)` step: alone, combined with
`register_passthrough_channel`, and combined with `set_ext_update_mode_sta`
(a distraction — that call just toggles an unrelated periodic status
heartbeat on a totally different `f0`...`f7` frame format, not the
`f3`...`f4` online-exec channel; harmless, cleanly reversible, unrelated to
mode). After the third frame's response, the device takes on the order of a
couple of seconds to actually settle — polling mode immediately afterward
still shows upload; polling again ~2s later shows online.

**Implemented** in `robot/cyberpi.py` as `ONLINE_ENTRY_SCRIPTS` +
`encode_online_entry_frames()` (pure encoding, no I/O, matching that
module's existing style), and wired into `estop.py`/`motion.py`'s
`initialize()`: if mode isn't online on the first check, both clients now
attempt this bootstrap sequence once, wait, and re-check — only raising
`CyberPiNotReadyError` if it still isn't online afterward. `telemetry.py` is
unchanged (never needed this). Six new/updated regression tests in
`tests/run.py` cover the encoder output shape, the successful-bootstrap
path, and the still-refuses-if-it-doesn't-take path, all against a fake
serial that mirrors this exact hardware behavior.

**Confirmed live, real hardware, real production code** (not just the ad-hoc
scripts used during discovery): `CyberPiEmergencyStopClient.open(port).initialize()`
against the actual board returns `CyberPiMode.ONLINE` cleanly.

Standalone Pixel motor control — the original goal of this whole
investigation — now works: no Mac, no mBlock, no prior online session
required. `estop.py`/`motion.py` can bootstrap themselves into online mode
from cold upload-mode boot.

## The complete vision, verified end to end, through the real running app

`drive_straight`/`turn`/`emergency_stop` tools were added to
`brain/gemma.py` alongside the existing `read_spatial_sensors` — bounded to
`MotionLimits` in their tool schemas, executed directly when Gemma calls
them (no separate permission-gate step, matching how the existing read-only
tools already work and exactly what `GEMMA_SYSTEM.md` already called for:
"the required safety interlocks report ready", not a confirmation dialog).

**Confirmed live, cold boot, real chat API, no Mac at any point**: the
CyberPi was power-cycled (back to upload mode), `main.py` relaunched on the
Pixel via `termux-usb -r -E -e "./run.sh" <device>`, and two real messages
sent to the actual running `/api/say` endpoint:

- *"HAL, check your ultrasonic distance sensor and tell me the reading."*
  → `"The ultrasonic distance sensor reading is 115.6 centimeters."` — a
  real value, read live.
- *"HAL, drive forward five centimeters at twenty percent speed."*
  → `"I have driven forward five centimeters at twenty percent speed."`
  — and the wheels actually turned, operator confirmed.

Both turns went through the complete real stack: text in, local Gemma
inference on the Pixel's own CPU (~39s per turn — expected, matches known
phone-CPU speed), a real tool call, real hardware I/O (the second one
self-bootstrapping from cold `upload` mode into `online` automatically,
mid-turn, with no special-casing needed), a real spoken reply via Piper.
No mBlock, no Mac, at any point in either turn.

## Still open
- An initial raw device-descriptor read via `os.read(fd, 18)` returned zero
  bytes (superseded — the working path is `libusb_control_transfer`, not a
  bare `read()` on the fd; not worth chasing further since device identity
  is already established from the Mac-side bring-up).
