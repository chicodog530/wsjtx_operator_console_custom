# WSJT-X Operator Console 1.1 — Technical Description

## Purpose

WSJT-X Operator Console is a local browser application that augments WSJT-X.
It does not implement an FT8 decoder or transmitter. WSJT-X remains the modem,
decoder, radio-control endpoint, and QSO engine.

## Architecture

The application is written in Python and runs a FastAPI web server on the local
computer. The user interface is HTML, CSS, and JavaScript served to a normal
web browser.

Primary components:

- **FastAPI / Uvicorn** — local HTTP and WebSocket server
- **WSJT-X UDP protocol interface** — receives Heartbeat, Status, Decode, Clear,
  Close, and related WSJT-X network messages; sends supported control messages
- **SQLite** — local QSO, decode, wanted-target, event, and propagation storage
- **ADIF monitor** — watches the configured WSJT-X `wsjtx_log.adi` file and
  imports new or changed records
- **Audio capture** — PortAudio through `sounddevice`
- **DSP** — NumPy FFT with Hann windowing, DC removal, calibrated dBFS scaling,
  75-percent overlap, power averaging, glitch rejection, and peak tracking
- **Waterfall transport** — compact binary WebSocket frames
- **Waterfall renderer** — WebGL circular texture with a Canvas fallback
- **NTP monitor** — compares the PC clock with a configurable NTP server
- **Elevated clock helper** — a separate one-shot process requests Windows
  administrator permission only when the user asks to set the system clock
- **PSK Reporter client** — retrieves reception reports when enabled

## Network ports

- Web interface: TCP port `8080`
- WSJT-X UDP input: UDP port `2237`
- NTP clock check: UDP port `123` outbound
- PSK Reporter and map tiles: normal outbound HTTPS

The application binds its web server to `0.0.0.0` by default, making the page
available to other devices on the same LAN if the Windows firewall allows it.
No cloud account or external server is required for the core application.

## WSJT-X integration

WSJT-X sends datagrams to the configured UDP address and port. Operator Console
parses those datagrams and updates its internal station state. Supported control
actions are sent back using WSJT-X network protocol messages. The application
does not modify WSJT-X program files.

## Audio and waterfall processing

The selected recording device is opened at its native sample rate when
possible. The DSP pipeline:

1. Captures mono floating-point audio samples.
2. Removes the DC component.
3. Applies a Hann window.
4. Computes a real FFT.
5. Corrects amplitude for the window coherent gain.
6. Converts spectral power to dBFS.
7. Uses 75-percent overlapping FFT frames.
8. Averages linear power before conversion to dB.
9. Rejects isolated implausible USB/audio glitches.
10. Reduces the 0–4000 Hz passband to browser-friendly bins while preserving
    narrow signals.
11. Sends signed 16-bit centi-dB values through a binary WebSocket frame.

The browser maps those values to a GPU-rendered color texture. New rows appear
at the top and older rows move downward. A 2D Canvas fallback is available when
WebGL2 is unavailable.

## Data storage

User data is stored outside the installation directory:

Windows:
`%LOCALAPPDATA%\WSJTX-Operator-Console`

Linux:
`~/.local/share/WSJTX-Operator-Console`

Files include:

- `settings.json`
- `dxassistant.db`
- `logs/command_center.log` and rotated backups

This design permits replacement of the program folder without losing settings
or imported QSO data.

## Security model

The main application runs without administrator rights. The Windows clock is
changed only by the separate `sync_clock_admin.py` helper after a UAC approval.
The web interface is intended for trusted home networks. It currently has no
login system, so users should not expose TCP port 8080 directly to the public
internet.

## Public-release privacy

The public package includes no default callsign, grid, ADIF path, or operator
database. A new user supplies station information during first-time setup.

## Version

`1.0.0` was the first public release. Version `1.1.0` adds client-side waterfall speed, zoom, pan, viewport-aware overlays, palettes, preference persistence, a minimap, and a detachable display. Earlier 3.x numbers were private
development builds and are not part of the public version history.


## Waterfall viewing controls in 1.1

The DSP backend continues to transmit the complete 0–4000 Hz passband. Zoom
and pan are client-side operations and therefore do not reduce the incoming
spectral information.

The WebGL fragment shader samples a selected normalized portion of the full
waterfall texture. Linear texture filtering and interpolated FFT-bin sampling
reduce blockiness at higher zoom factors. The visible frequency ruler and all
overlay coordinates are calculated from the same viewport.

Waterfall speed is implemented with a fractional row accumulator. At 65%, for
example, the renderer advances approximately 0.65 history rows per incoming
FFT update. Values above 100% may write multiple interpolated rows.

A detached waterfall is another browser window connected to the same FastAPI
and audio backend. It does not open a second recording device.

Browser-local preferences include speed, zoom, view center, Follow RX,
palette, floor, auto-floor, ceiling, averaging, and peak hold.
