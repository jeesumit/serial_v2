# Serial Monitor — Windows desktop app (v1.2.0)

Native-window serial monitor: live Print / Plot / Both, time-split log files (CSV, JSON, TXT, BIN), open saved logs to print or plot,
VID/PID port details. Branded with the Escorts Kubota logo (icon + header). Built for locked-down corporate PCs: loopback-only, no
outbound network, token-protected, validated inputs — see **IT_SECURITY_NOTES.md**. Test evidence: **TEST_REPORT.md**.

## Features
* New log file every 1 / 5 / 10 / 15 / 20 / 30 / 40 / 45 / 50 / 60 / 90 / 120 min (or single file); one folder per session; files are closed on schedule.
* **Log folder** can be changed in the app (e.g. to an approved location); default `%LOCALAPPDATA%\SerialMonitor\logs`. 📁 opens it.
* 📂 **Open file** (or drag & drop): `.csv .json .txt .bin` from this app, any CSV with a header row; several parts merge in time order → Print table (paged, filter) / Plot / Both; 🖨 Print.
* Baud stepper (◀ ▶, wheel, presets, custom), data bits / parity / stop bits, Hex or Text input, quick commands, command history, zoom/pan plot with tooltip.

## Build the installer
**GitHub (no tools needed):** upload this folder's contents (keep `.github/workflows/`), then Actions → *Test, build and publish Windows installer* → Run workflow.
The workflow runs all tests on Windows + Linux first, builds, self-tests the packaged exe (also with a proxy configured), runs a silent install → run → uninstall test,
then publishes `SerialMonitor-Setup-1.2.0.exe`, a portable zip, `SHA256SUMS.txt` and `requirements-lock.txt` on the **Releases** page.
**Local Windows PC:** Python 3.11 (+ Inno Setup 6) → `build_windows.bat`.

## Run the tests yourself
`run_tests.bat` (Windows) / `./run_tests.sh` — 241 automated tests (Python + browser UI).

## Dev
`python app.py` (window) · `--browser` · `--selftest` · `--gui-selftest` · `python server.py` (browser mode, prints a one-time URL with the access token).
Samples: `samples\` (all formats + a 3-part split set). Decode BIN: `python server.py --decode file.bin`.
Unsigned installer: SmartScreen → *More info → Run anyway*, or sign it (see IT notes). USB-serial drivers (CP210x/FTDI/CH340) are separate.
