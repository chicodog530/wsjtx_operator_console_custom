# WSJT-X Operator Console 1.1

WSJT-X Operator Console is a browser-based companion for WSJT-X. It displays
live WSJT-X status and decodes, a calibrated audio waterfall, DX and band
advisors, logbook statistics, maps, PSK Reporter information, and supported
WSJT-X control actions.

It does not decode FT8 itself and it does not replace WSJT-X. WSJT-X must be
running in the background.

## Start here

Windows users should open `START_HERE.html` first. It contains plain-language,
step-by-step setup instructions with exact menu names and settings.

Start the program by double-clicking:

`run_windows.bat`

The browser normally opens at:

`http://127.0.0.1:8080`

## Waterfall controls

- **Speed:** 10%–200%; the default is 65%.
- **Zoom:** 1×–8×.
- **Drag:** pan left or right.
- **Mouse wheel:** zoom around the pointer.
- **Shift + mouse wheel:** pan horizontally.
- **Follow RX:** center the view on the WSJT-X receive frequency.
- **Minimap:** click anywhere to reposition the visible window.
- **Detach:** open the waterfall in a separate resizable browser window.
- **Palette:** Classic SDR, High contrast, or Grayscale.

Waterfall preferences are remembered by the browser.

## User data

Settings, the imported logbook database, and logs are stored outside the
program folder so upgrades do not erase them:

`%LOCALAPPDATA%\WSJTX-Operator-Console`

## Documentation

- `START_HERE.html` — beginner setup and troubleshooting
- `TECHNICAL_DESCRIPTION.md` — architecture and implementation details
- `CHANGELOG.md` — complete public release history

Version 1.1 contains no default callsign, grid, logbook, or personal station
information.
