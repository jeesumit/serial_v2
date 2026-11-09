# Serial Monitor — notes for IT / security review (v1.2.0)

**Purpose:** view, plot and log data from serial (COM / tty) ports. Desktop app = a local Python backend + an embedded web view (Microsoft Edge WebView2).

## 1. Network behaviour
| Item | Behaviour |
|---|---|
| Listening | **127.0.0.1 only** (fixed TCP port 47653, random free port if busy). Starting the server on any other address is refused unless `--allow-remote` is passed (browser mode only; the desktop app never does). |
| Outbound | **None.** No telemetry, update checks, DNS lookups, licence calls or cloud services. Verified: the running app holds exactly one loopback LISTEN socket and zero outbound connections. |
| Firewall | Loopback traffic does not trigger Windows Firewall prompts. |
| Proxies | The app's own loopback client ignores `HTTP(S)_PROXY`; the embedded browser never proxies 127.0.0.1. Tested with a dead proxy configured. |

## 2. Local access control (protects shared / RDS machines and drive-by web pages)
* Random per-launch **access token** (192 bit) required on every request (HttpOnly, SameSite=Strict cookie or `X-SM-Token` header). Other local users or processes without the token get HTTP 401/403 and cannot open or read your serial ports.
* **Host** header must be `127.0.0.1:<port>` / `localhost:<port>` (blocks DNS-rebinding); **Origin** must match (blocks cross-site requests); POST requires `application/json`; no CORS headers are sent.
* Strict **Content-Security-Policy** (`default-src 'none'`, connect only to itself, no framing), `nosniff`, `no-store`, `no-referrer`.
* Inputs are allow-listed/validated: port names (`COMn`, `\\.\COMn`, `/dev/...`, `SIMULATOR` — URL-style names such as `socket://` are rejected), baud 50–4,000,000, frame settings, log format, split interval, request size ≤ 64 KB, slow clients dropped after 30 s. Errors never expose stack traces.
* No `eval`, `exec`, shell execution, pickle or dynamic code loading. Device data is only ever rendered as text (tested with HTML/script/prototype-pollution payloads).

## 3. What gets installed / written
* **Installer:** Inno Setup, **per-user by default (no admin)** to `%LOCALAPPDATA%\Programs\Serial Monitor`; an admin can choose "all users" in the first dialog (→ `Program Files`, recommended when AppLocker blocks user-writable paths). Creates Start-menu (+ optional desktop) shortcuts and a standard Uninstall entry. **No services, drivers, scheduled tasks, startup entries or registry autoruns.** Installs the WebView2 runtime only if missing.
* **Data:** `%LOCALAPPDATA%\SerialMonitor\` (local, not roamed/redirected): `logs\`, `settings.json`, `webview\` (browser profile), `app.log`. The **log folder can be changed in the app** (e.g. to a DLP-approved or encrypted location) and is remembered. Uninstall keeps user data.
* **Log files** are plain CSV/JSON/TXT/BIN containing whatever the device sends — classify per your policy; the app does not encrypt them. Files are split on a timer (1–120 min) and each file is **closed on schedule, even when idle**, so backup/DLP agents can scan finished files. Only the log folder, `settings.json` and the profile folder are written.

## 4. Allow-listing (AppLocker / WDAC / Defender / SmartScreen)
* The exe carries a version resource (Company, Product, Version) and an icon; no UPX packing; one-folder build (fewer AV false positives than one-file).
* **Unsigned by default → SmartScreen "unknown publisher".** Options: (a) sign `SerialMonitor.exe` and the setup with your code-signing certificate (recommended), (b) allow-list by **SHA-256** (`SHA256SUMS.txt` is published with each release), (c) path rule for the install folder.
* The app loads its Python runtime and `.pyd/.dll` files from its own install folder; rules must allow that folder. WebView2 (Evergreen) is managed by IT or installed by the setup.

## 5. Components (SBOM)
Python 3.11 runtime, pyserial, pywebview (+ pythonnet on Windows), Microsoft Edge WebView2, PyInstaller bootloader. The exact versions of every release are in `requirements-lock.txt`. At build time: `pip-audit` → no known vulnerabilities; static scan (bandit) → 0 findings (6 justified suppressions).

## 6. Residual risks / limits
* Another process **running as the same Windows user** can already read that user's memory and files; the token cannot defend against that.
* Logs are unencrypted at rest; the last ≤30,000 records are also held in RAM while connected.
* Unsigned until you sign it. Real serial hardware, GPO rules and your specific DLP product were not available to the author for testing — see `TEST_REPORT.md`.

## 7. Verify it yourself
```
Get-FileHash .\SerialMonitor-Setup-1.2.0.exe -Algorithm SHA256        # compare with SHA256SUMS.txt
Get-NetTCPConnection -OwningProcess (Get-Process SerialMonitor).Id    # expect one Listen on 127.0.0.1, nothing else
```
Process Monitor / Wireshark: no outbound traffic; file writes only under the folders in section 3.
