# Changelog

All notable public changes to WSJT-X Operator Console are documented here.

## 1.2.0 — POTA Integration

### Added
- **Major Performance Improvements:** Resolved an issue where large ADIF imports and busy decoding cycles caused 100% CPU usage by adding missing database indexes, debouncing UI updates, and caching expensive queries.
- Live Parks on the Air (POTA) integration! The app now automatically queries the official POTA API behind the scenes.
- Active POTA stations spotted on the network receive a distinct green `POTA [Park ID]` badge on the Activity cards and Smart DX Top 5 Queue.
- Smart message parsing for POTA: even if a station is not officially spotted, if their message contains "CQ POTA", they will receive a generic `POTA` badge.
- **POTA DX Hunter Preset:** Added a convenient one-click preset button to quickly filter for active POTA stations.
- **Persistent UI Settings:** All Smart DX Advisor checkbox and continent filter preferences are now automatically saved to your browser so they persist across sessions.

## 1.1.0 — Waterfall controls and viewing tools

### Added
- Waterfall speed slider from 10% to 200%.
- Slower 65% default waterfall speed.
- Horizontal waterfall zoom from 1× to 8×.
- Drag-to-pan while zoomed.
- Mouse-wheel zoom centered on the pointer position.
- Shift + mouse wheel horizontal panning.
- Optional **Follow RX** mode that centers the visible passband on the WSJT-X RX frequency.
- Full 0–4000 Hz minimap with a visible-window indicator.
- Clickable minimap for quick repositioning.
- Classic SDR, high-contrast, and grayscale palettes.
- Reset View button.
- Detachable waterfall window for a second monitor.
- About dialog with version information and documentation links.
- Persistent waterfall preferences stored in the browser.
- In-app links to the complete changelog and technical description.

### Improved

- Waterfall interpolation now samples between FFT bins instead of selecting only one bin.
- WebGL uses linear texture filtering while zoomed.
- Frequency ruler automatically chooses sensible tick spacing for the visible range.
- Decode labels, signal tracks, selected-frequency markers, and cursor frequency now respect the zoomed viewport.
- Canvas fallback supports the selected zoom, pan, and palette for new rows.
- Waterfall help text now describes drag, wheel, Shift + wheel, and double-click controls.

### Fixed

- Waterfall history no longer advances several rows per received frame at the normal setting.
- Selected frequency and nearest-decode calculations now use the zoomed frequency range.
- Public version and About information are synchronized at 1.1.0.

### Notes

- Preferences are stored per browser profile using local storage.
- The detachable window displays the same live backend stream and does not start a second audio capture.
- WebGL2 remains preferred; a Canvas fallback is retained.

## 1.0.0 — First public release

### Added
- **Major Performance Improvements:** Resolved an issue where large ADIF imports and busy decoding cycles caused 100% CPU usage by adding missing database indexes, debouncing UI updates, and caching expensive queries.

- Live WSJT-X status, decodes, activity views, maps, awards, and history.
- Smart DX Advisor and Smart Band Advisor.
- PSK Reporter retrieval.
- Native audio waterfall.
- ADIF log monitoring and importing.
- NTP clock-drift monitoring and separate elevated Windows clock helper.
- Plain-language `START_HERE.html` setup instructions.
- Technical architecture document.
- Public-safe defaults with no callsign, grid, personal logbook, or station-specific data.
