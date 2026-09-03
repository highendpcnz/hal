# Project Odyssey Robot Control Contract

This is the first, model-free control-plane contract for the Pixel-to-mBot2
integration. It is deliberately provisional: the proposed wire format must be
checked against the CyberPi firmware and a real USB capture before a driver is
connected to motors.

## Logical commands

The host exposes only bounded, typed operations: `drive_distance` (centimetres
and speed percentage), `rotate` (degrees and speed percentage), `read_telemetry`,
and `emergency_stop`. Forward motion requires a recent telemetry sample and an
obstacle distance greater than the configured interlock threshold. The default
bench limits are 50 cm, 30% speed, 180 degrees, and 8 cm minimum clearance.

## Provisional frame format

Frames are `AA 55 | message_id | payload_length | payload | crc16`, with a
maximum 32-byte payload. The CRC-16/CCITT-FALSE covers `message_id`, length,
and payload and is encoded little-endian. Telemetry uses a 16-byte payload:
left/right encoder ticks, yaw/pitch in tenths of a degree, distance in
millimetres, and battery in millivolts.

## Observed CyberPi transport

On the probe Mac, the device enumerates as USB vendor `6790` (`0x1a86`),
product `29987` (`0x7523`), and mBlock opens `/dev/tty.usbserial-110` at
115200 baud. Its bundled CyberPi profile names the mode protocol `f3f4`. The
frame shape is `f3 | header_sum:u8 | payload_length:u16le | payload |
payload_sum:u8 | f4`, where `header_sum` is the additive checksum of `f3` and
the two length bytes, and `payload_sum` is the additive checksum of the
payload. This is implemented as a read-only codec in `robot/cyberpi.py`; the
second byte is not a frame type. The local mBlock source constructs online
payloads beginning with `0x28`, followed by a service byte, a little-endian
sequence number, a little-endian UTF-8 script length, and the script bytes.
`robot/cyberpi.py` can build and parse this payload and its surrounding frame
without opening a device. The helper is structural only; it does not execute
scripts or assert that a particular mBot2 API call moves the hardware.
The observed markers are online `f3 f6 03 00 0d 00 01 0e f4` and upload
`f3 f6 03 00 0d 00 00 0d f4`. Motor/telemetry command semantics are not yet
verified. A live, non-mutating mode query returned `f3 f6 03 00 0d 80 01
8e f4`, which identifies the current online/debug mode as `0x01`. Do not
send markers or commands from HAL except through an explicitly reviewed
bring-up step. A live firmware query returned `44.01.016`; this identity is
now a regression fixture, but it does not yet validate motor APIs. A temporary
`subscribe.add_item` probe also returned `{'od_probe_1': 300.0}` and was then
removed with `subscribe.del_item`. The installed mBlock ultrasonic extension
treats values at or above `300` as out of range, so this is a valid no-target
result rather than proof of a sensor fault. With a target about 10 cm away, a
direct wait-for-response query, `mbuild.ultrasonic2.get(1)`, returned `10.0`
twice; querying indexes 1 through 8 returned `[10.0, 0, 0, 0, 0, 0, 0, 0]`.
A subscription created after that direct read returned `10.5`. The initial
`300.0` was therefore a stale or uninitialized subscription value, not a
wiring failure. Prime and validate each sensor with a direct read before using
its subscription stream for a safety decision. Both response forms are now
regression fixtures. The mBlock-host helper `status_check` is not available in
CyberPi's standalone online namespace: attempting to call it produced a framed
`{"err":"NameError"}` response while the next direct ultrasonic read still
returned `11.3`. The driver decodes these structured errors separately from
transport failures.

**Hazard, hardware-confirmed**: `uos.listdir()` and `uos.getcwd()` — genuine,
standard MicroPython filesystem calls, present in `dir(uos)` and otherwise
unremarkable — cause the online-exec handler to stop responding entirely,
including to completely unrelated, already-verified read-only queries
(`cyberpi.get_battery()`) issued afterward. The board's main program and
screen kept running normally throughout (this is a stuck online-exec request
handler, not a device crash), and it did recover on its own after roughly
10 seconds in the one case tested — but that recovery was not verified to be
reliable, and no other real filesystem call (`stat`, `mkdir`, `rename`, ...)
has been tried at all given this result. Treat any `uos.*` filesystem call
over the online-exec channel as unverified and avoid it; this is a different
and worse failure mode than the clean `NameError` responses every other
unavailable name in this namespace produces.

## The online-exec namespace, mapped (safe reads only)

`dir()`/`globals()`/attribute access inside the online-exec sandbox are safe
(no I/O, same risk class as every other verified read-only query) and reveal
a real, rich API surface — `str(list(globals().keys()))` is the way to see
it, one query at a time to stay under the 249-byte limit. Confirmed live:

- `makeblock.get_mp_mode()` currently returns `1` = **`MP_MODE_REPEL_FRIENDLY`**
  (not `MP_MODE_USER_SCRIPT`=5, `MP_MODE_ONLINE_SCRIPT`=7, or
  `MP_MODE_FACTORY_SCRIPT`=8). "REPEL" reads as a typo/abbreviation for
  **REPL** — the entire online-exec channel this whole project is built on
  has been talking to a "friendly" (line-based, human-readable) REPL mode
  the whole time, not a script-slot state.
- `makeblock.get_system_mode()` currently returns `1` =
  `SYSTEM_MODE_OFFLINE` (not `SYSTEM_MODE_ONLINE`=2) — a separate, higher-level
  concept from the `f3f4` online/upload marker `robot/cyberpi.py` already
  decodes; the two are not the same bit and their exact relationship isn't
  mapped yet.
- **`MP_MODE_REPEL_RAW = 2`** exists as a distinct mode. "Raw REPL" is
  MicroPython's own standard machine-readable protocol — what `mpremote`/
  `ampy`-style tools use for real file transfer — and is almost certainly
  the actual switch needed for persistent-script upload, matching what the
  parallel `edge-robotics-stack` project found necessary (flipping a
  `repl_enable`-style flag) by a different route.
- `nvs`/`nvs_o` is ESP32 key-value flash storage for config
  (`WIFI_SSID`, `WIFI_PASSWORD`, `OTA_*`), not program storage.
  `project_operation` and `sketch` are red herrings by name — the former is
  a small internal utility object, the latter is turtle-graphics/screen
  drawing.

**Not yet attempted, and deliberately so**: calling `makeblock.set_mp_mode(2)`
to actually switch into raw REPL mode. Unlike everything above, this is a
state-changing command, not a read — it would change how the device
interprets serial data, likely making the current `f3f4` online-exec channel
itself unavailable for the duration, and was not attempted without it being
a deliberate, explicitly-agreed step, the same as every other hardware
"first" in this project.

**`makeblock.get_temporary_script()`, checked live, one query, generous
10s timeout given the earlier hang**: returned promptly (well under the
timeout, no hang), `result=''`. No script currently occupies whatever slot
this getter reads — consistent with this board's existing on-device program
being the *main*/factory-loaded one (`MP_MODE_FACTORY_SCRIPT`-adjacent),
not something sitting in this particular "temporary script" slot.

**`str(dir(makeblock))`, checked live, one query, no hang**: gave the
complete member list (`dir()`/reflection only, no I/O — same safe class as
every other read here). The full, relevant subset:

```
get_mp_mode / set_mp_mode
get_system_mode / set_system_mode
get_raw_mode / set_raw_mode
get_temporary_script / set_temporary_script
get_firmware_version
get_backtrace_str / get_backtrace_str_flag / set_backtrace_str_flag
print_sys_mem_info
restart   (RESTART_TYPE_NONE / _FAST_RESTART / _WHOLE_RESTART)
stop_script
nvs                          # ESP32 key-value flash (WIFI_*, OTA_* config), not program storage
setup, sleep_special, event, communication, drivers, wifi, wifi_mesh,
wifi_ota, espnow, ble, ...   # peripheral/subsystem namespaces, not script storage
```

Two takeaways:

1. There is exactly **one** script-text slot exposed here —
   `get_temporary_script()`/`set_temporary_script()` — not one per mode.
   That plus the existence of `set_mp_mode()` strongly suggests the real
   persistence mechanism is: write script text via `set_temporary_script()`,
   then `set_mp_mode(...)` to tell the firmware what to do with it. This is
   still a hypothesis, not confirmed — the exact mode value and sequencing
   mBlock's own uploader uses is unknown.
2. `raw_mode` (`get_raw_mode()`/`set_raw_mode()`) is a **separate**
   getter/setter pair from `mp_mode`, not just another name for
   `MP_MODE_REPEL_RAW`. Read earlier as `0`. Whether setting it is what
   actually switches the serial interpreter into raw-REPL, versus
   `set_mp_mode(MP_MODE_REPEL_RAW)` doing that, versus the two being
   related-but-independent, is not yet known.
3. Nothing here is a general filesystem API — confirms the `uos.*` hang
   documented above is a different, separate namespace, not something this
   `makeblock`-mediated path would also trigger.

**Decision: investigation closed here, deliberately.** `set_mp_mode`,
`set_raw_mode`, `set_temporary_script`, and `restart` remain untried, and
that's the intended end state, not a gap to fill later. Two reasons: (1)
every write above is a guess at sequencing/values Makeblock's own mBlock
uploader is the tested, vendor-supported path for, on a board that has
already hung once this session on an innocuous-looking call; (2) HAL
doesn't need persistent script upload at all — its whole model is live
online-exec commands sent in real time over the already hardware-verified
`f3f4` channel (see `docs/termux-usb-bringup.md`), not a resident program
pushed onto the board. If persistent upload is ever wanted for some other
reason, do it via mBlock on the Mac, not by guessing at this API from
Termux.

**Correction to the reasoning above, found later the same session**: "works
end-to-end from the Pixel today, independent of reported mode" turned out
to be true only for *reads*. A real `drive_straight()` sent from the Pixel
while the board reported upload mode was silently acknowledged with no
error and produced zero physical movement — see the "Reverted" writeup in
`docs/termux-usb-bringup.md`. `estop.py`/`motion.py` now require genuine
online mode again. This means standalone Pixel motor control is currently
*blocked*, not merely unverified — getting it working for real, without a
Mac/mBlock session first, would specifically need a verified way into
online mode, which loops back to the very `set_mp_mode`/`set_raw_mode` API
this section just decided not to touch. That tension is real and unresolved;
re-opening that investigation is a decision for the user to make
deliberately, not something to resume unprompted on the strength of this
paragraph's original reasoning.

**Resolved, without touching `set_mp_mode`/`set_raw_mode` at all.** The
actual answer was `online_restart` plus one prerequisite
(`config.write_config("repl_enable", False)`) — a different, previously
untried part of the online-exec namespace, found via USB packet capture of
a real mBlock session rather than by guessing at the `makeblock` write API.
Full writeup, the exact sequence, and the wrong hypotheses ruled out along
the way are in `docs/termux-usb-bringup.md`'s "Resolved: entering genuine
online mode without mBlock, at all". Standalone Pixel motor control works
now; `estop.py`/`motion.py` self-bootstrap into online mode from cold
upload-mode boot when needed.

The f3f4 length field can represent a much larger payload, but the live online
executor has a smaller script buffer. A read-only battery expression padded to
249 UTF-8 bytes returned a valid framed response; the same expression at 250
bytes and above returned no frame and leaked an ASCII tail. Host code therefore
rejects online scripts longer than 249 encoded bytes and should keep telemetry
queries small rather than aggregating many getters into one expression.

Getter-only chassis bring-up also succeeded without sending a motor command.
The live baseline reported battery `100`, attitude `[pitch 0, roll 2, yaw 44]`,
encoder speeds `[-0.0, -0.0]`, encoder powers `[-0.0, -0.0]`, and encoder
angles `[15, 0]`. These values prove that the CyberPi mBot2 getter interface is
reachable; they are observations, not calibrated limits or motion validation.

`robot/telemetry.py` implements this read-only bring-up as a sequence-matched
serial client. It verifies online mode and firmware, primes ultrasonic index 1
with a direct read, then polls distance, battery, attitude, encoder speed,
power, and angle using bounded getter scripts. It exposes no motor methods.
Run a one-shot bench snapshot with:

```sh
python -m robot.telemetry --port /dev/cu.usbserial-110 --samples 1
```

The CLI emits newline-delimited JSON and closes the serial port on success or
failure. `pyserial` is imported only when hardware is opened, so model-free
tests can use the deterministic fake transport without a connected robot.

## Termux USB handoff (Android/Pixel deployment)

The `--port /dev/cu.usbserial-110` examples above are the desktop (macOS/Linux)
path pyserial opens directly. Android exposes no such device node for USB-serial
adapters — `termux-usb` instead grants a raw, already-open file descriptor via
a one-time permission dialog. `robot/android_usb.py`'s `Ch340UsbTransport`
implements the same `SerialTransport` protocol against that fd directly,
bypassing pyserial. `brain/gemma.py`'s `_open_telemetry_client` and
`_open_robot_transport` check the `TERMUX_USB_FD` environment variable at
runtime: if set, they build a `Ch340UsbTransport` from it; otherwise they fall
back to the pyserial path above, which does not exist on Android and fails
every telemetry/motion tool call with `[Errno 2] No such file or directory`.

`TERMUX_USB_FD` is only set when the process is launched under:

```sh
termux-usb -r -E -e "<command>" <device-path>
```

(`-r` requests the permission dialog if needed; `-E` exports the fd as
`TERMUX_USB_FD` instead of appending it as a CLI arg; `-e <command>` is the
process to run.) The wrapped command must stay in the foreground of the whole
process chain — if the script `termux-usb` launches backgrounds the real
server and then exits, `termux-usb` reclaims the fd once its direct child
exits, even though the actual server process is still running.

Confirmed live 2026-09-04: an instance of `main.py` started via a plain
`nohup ./run.sh` (no `termux-usb` wrapper) ran for roughly two hours with the
CyberPi fully connected, enumerated, and powered on, and every
`read_spatial_sensors` call failed with the pyserial fallback's "No such file"
error — nothing in the server logs surfaced this beyond a generic tool-call
failure event; it only showed up in the per-session transcript under
`data/brain/gemma/<session>.json`. `~/launch_v4b.sh` now auto-discovers the
device via `termux-usb -l` and wraps `run.sh` in `termux-usb -r -E -e ...` so
`TERMUX_USB_FD` reaches `uvicorn`. Verify a live launch actually has the fd
with:

```sh
cat /proc/<uvicorn-pid>/environ | tr '\0' '\n' | grep TERMUX_USB_FD
```

An empty result means the server is running without CyberPi access — every
telemetry and motion tool call will fail until it is relaunched under the
`termux-usb` wrapper.

## Stop (`robot/estop.py`)

The bring-up order below calls for verifying stop before any movement. The
mBot2 Python API's documented all-motors stop is `cyberpi.mbot2.EM_stop(port =
"all")` — confirmed against Makeblock's mBot2 Python curriculum booklet
(`forward`/`backward`/`straight`/`turn_left`/`turn_right`/`drive_power` are
documented alongside it for the later motion contract, but only `EM_stop` is
implemented so far). `CyberPiEmergencyStopClient` is a separate class from
`CyberPiTelemetryClient`, not one more method on it: the telemetry client's
read-only guarantee is enforced by which class you import, and a regression
test (`tests/run.py`) already asserts the telemetry client never emits
`EM_stop`, `EM_set`, `drive`, `straight`, or `turn(`. The stop client mirrors
the same verified online-mode/f3f4 session handling but exposes exactly one
capability — `stop_all()` — and refuses to run it before `initialize()`
confirms online mode. Bench-test it directly with:

```sh
python -m robot.estop --port /dev/cu.usbserial-110 --confirm
```

`--confirm` is required: this is the first command in the codebase that is
actually transmitted to the real chassis rather than only queried from it.
Confirmed against the live robot with the chassis secured: `stop_all()` sent
`EM_stop(port='all')`, CyberPi returned no error, and a follow-up telemetry
read showed `motors_stationary: true`.

## Heartbeat loss (`robot/watchdog.py`)

`SafetyController.watchdog_expired()` was previously only exercised against
the simulator; nothing polled it on a schedule or acted on the answer.
`HeartbeatWatchdog` is that missing loop: poll it, and the instant a heartbeat
goes stale it latches the controller's estop and calls the real
`CyberPiEmergencyStopClient.stop_all()` — a lapsed heartbeat now reaches the
chassis, not just host state. Confirmed live: heartbeating for 0.6s at a
0.25s watchdog window sent zero stops; withholding heartbeats fired a real
`stop_all()` at 0.285s, `controller.estopped` latched `True`, and a follow-up
telemetry read confirmed the chassis stayed stationary throughout (it already
was — no drive command exists yet).

This proves only the host-side half of heartbeat loss: the host correctly
detects a stale heartbeat and transmits a real stop while it still has a live
serial link. It does **not** prove CyberPi's firmware would stop the chassis
on its own if the host process or USB link vanished entirely while the robot
was actually moving — that has no real content to test until a bounded real
drive command exists, so it is deferred to the movement bring-up step below:
issue a low-speed drive with the wheels raised, then physically interrupt the
host (kill the process or pull the USB cable) mid-command and observe whether
the wheels keep spinning. Until that test runs, treat the firmware watchdog
in "Safety invariants" below as unverified, not assumed.

## Movement (`robot/motion.py`)

`CyberPiMotionClient` is the first code in this repository that can actually
move the chassis. It exposes exactly two commands, both mapped to verified
mBot2 Python API primitives — confirmed against Makeblock's own mBot2 Python
curriculum booklet, not guessed:

- `drive_straight(distance_cm, speed_pct)` → `cyberpi.mbot2.straight(distance_cm,
  speed = speed_pct)`
- `turn(angle_degrees, speed_pct)` → `cyberpi.mbot2.turn(angle_degrees, speed =
  speed_pct)`

These were chosen deliberately over `forward()`/`backward()` (documented by
Makeblock as running *forever* with no `run_time` — "should only be used when
the ultrasonic sensor... [is used] to control when the motors should stop")
and over `turn_left()`/`turn_right()` (time-bounded via `run_time`, which races
a manually supplied timer against the real move). `straight()`/`turn()` are
bounded by the quantity that actually matters — distance, degrees — and
self-terminate on the CyberPi firmware side without the host timing anything.

Every call is validated by `SafetyController.prepare_drive`/`prepare_turn`
(safety.py) — the exact same bounds the simulator already enforces — before
anything is encoded or transmitted; a regression test confirms a command
exceeding the configured limit produces zero bytes on the wire.

**Open and unresolved:** once a `straight()`/`turn()` script is sent, this
client blocks waiting for its response. Whether CyberPi's online executor can
run a second script — e.g. a stop — concurrently with an in-flight one, queues
it until the first finishes, or rejects it outright, has not been tested.
Until it has, treat every motion command as uninterruptible once sent, and
rely on small bounded distances/angles/speeds — not a mid-flight stop — as the
actual safety margin for a first test.

**Status: confirmed live**, wheels raised and clear, in two runs. First,
`drive_straight(5, 20)`: encoder angles moved left `14° → 102°`, right
`0° → -85°` (equal and opposite, as a straight drive should look), motors
stationary again immediately after — operator confirmed smooth, no odd
noises. Second, `drive_straight(30, 25)` — a longer run chosen so the motion
was easy to watch throughout: took 3.81s, encoders moved left `102° → 632°`,
right `-85° → -615°` (again equal and opposite), motors stationary
immediately after — operator confirmed smooth the whole way through, no
drift. Third, `turn(90, 25)`: took 1.31s, encoders moved left `632° → 792°`
(+160°), right `-615° → -456°` (+159°) — near-equal magnitude, same-signed
delta (the right signature for a turn, versus drive's opposite-signed delta).
Yaw stayed exactly `109.0 → 109.0`, unchanged — expected, since the chassis
itself never reorients with the wheels raised off the ground, only the wheels
spin. Motors stationary immediately after; operator confirmed a smooth ~90°
turn.

**Real-world left/right direction, confirmed later, wheels down on a clear
floor** (the run above couldn't show this — raised wheels spin without the
chassis ever reorienting): `turn(90, 8)`, called directly against real
hardware bypassing Gemma entirely, physically pivoted the chassis to the
**right**. So **positive `angle_degrees` turns right, negative turns left** —
this was genuinely unknown before this test; the tool's own description
deliberately only said "one way" / "the other" until this was pinned down.

All three of the above ran during a Mac/mBlock Live session — i.e. with the
board in genuine online mode. `drive_straight(5, 20)` sent from the Pixel
while the board reported *upload* mode instead was hardware-confirmed to
behave completely differently: a clean, error-free online-exec response,
and zero physical movement, confirmed twice with the operator watching.
`estop.py`/`motion.py` required online mode as a result — but now
self-bootstrap into it via `online_restart`/`config.write_config`, found
through USB packet capture of a real mBlock session (see
`docs/termux-usb-bringup.md`, "Resolved: entering genuine online mode
without mBlock, at all"). Real motor control from a Pixel-only session,
with no prior Mac/mBlock session, is hardware-confirmed working —
`drive_straight(5, 20)` sent after the bootstrap sequence actually moved
the wheels, confirmed twice.

## Safety invariants

The host starts emergency-stopped. Motion requires an explicit arm operation,
an active connection, valid telemetry, and a heartbeat within 250 ms. Invalid
or blocked commands are rejected before transmission; disconnects and expired
heartbeats latch the host-side emergency stop. The firmware must independently
enforce the watchdog and proximity cutoff; the host guard is not a substitute
for it.

## Bring-up order

Validate USB enumeration and firmware identity first, then telemetry, stop,
heartbeat-loss behavior, and only then a low-speed movement with the wheels
raised. Motion, missions, and autonomous navigation remain outside this
contract until these tests pass.

Status: USB enumeration, firmware identity, telemetry, stop, the host-side
half of heartbeat loss, and both bounded motion primitives (`drive_straight`
and `turn`) are all confirmed live (see the sections above), wheels raised
throughout. What remains: whether the firmware can be interrupted mid-command
(e.g. by physically pulling the USB cable during a longer drive) is still
untested and would need its own deliberate, separately-agreed step — as would
ever driving with the wheels actually on the ground.

## Vision (`capture_visual_scene`)

Deliberately outside the motion contract above: `capture_visual_scene`
(`brain/gemma.py`, `robot/camera.py`) is a read-only tool, offered to Gemma
only when `HAL_GEMMA_MMPROJ` is set. It shells out to `ffmpeg`'s avfoundation
input against the dev Mac's built-in camera as a stand-in for the Pixel's own
camera — no CyberPi/mBot2 hardware is involved, and it does not touch the
safety controller or the serial link. Every captured frame is also written to
`data/viewscreen/`, so it appears on the Bridge for Dave via the existing
drop-folder convention, not a bespoke display path.
