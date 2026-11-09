# Test report — Serial Monitor 1.2.0

Run on the final source. Sandbox: Linux x64, Python 3.12, Node 22 (a single-core VM). The same suite is the **gate in the Windows CI workflow**: `build` never starts unless `test` passes on `windows-latest` and `ubuntu-latest`.

## Results
| Suite | Tests | Result |
|---|---|---|
| `test_logging` — writers, **all 12 split intervals x 4 formats**, rotation edge cases, disk failure, unicode/spaced paths | 63 | pass |
| `test_assembler` — LF/CR/CRLF/mixed, 700 random chunkings per property, garbage bytes | 13 | pass |
| `test_api_security` — token, Host/Origin, headers/CSP, 34 bad-input bodies, traversal, oversize, concurrency, log folder | 90 | pass |
| `test_monitor` — simulator sessions, leaks, memory bound, export, failures, **real termios ports** (flood, unplug, frames) | 31 | pass |
| `test_app_launcher` — data-folder fallback, port choice, self-test, proxy variables | 7 | pass |
| `test_packaging` — version, icons, spec, installer script, workflow, code hygiene | 11 | 10 pass, 1 skipped (Windows-only check, runs in CI) |
| Browser UI (`ui.test.js`, real `index.html` in jsdom) — baud widget, connect, live data, hostile data, Print/Plot/Both, open-file + paging, print, history, log folder | 18 | pass |
| File parsers (`parsers.test.js`) — 4 formats identical, CSV quoting, generic CSV, fuzzing, 200k-row speed, 9 time zones | 8 | pass |
| **Total** | **241** | **240 pass, 1 platform-skipped, 0 fail** |

## Packaged build (PyInstaller, Windows spec)
* Frozen app self-test (UI, access control, ports, simulator, time-split log): **pass**, also with a dead corporate proxy configured.
* Socket audit of the idle packaged app: one loopback LISTEN socket, **zero outbound connections**.
* No display available: app logs the cause and falls back to the browser instead of crashing.

## Performance (45 s soak, real termios port, UI stream attached)
2,933 lines/s (~95 KB/s, above a fully loaded 921,600-baud link): **132,002 records logged for 132,000 lines sent, 0 dropped, 0 lost**; server CPU avg 6.4 % / max 8 % of one core; HTTP status latency max 6 ms; memory plateaus at the 30,000-record ring (+19 MB). 60,000-line floods in every log format: no loss, UI responsive (<0.5 s).

## Security scans
bandit: 0 findings (6 justified suppressions, listed in code). pip-audit (shipped dependencies): no known vulnerabilities. npm audit (test-only): 0.

## Defects found by testing and fixed in this release
1. Devices that end lines with **CR only** never produced a line (shown as "[partial]") — now LF/CR/CRLF/mixed.
2. Two sessions started within the same second **overwrote** the first session's log — unique folders.
3. Device-supplied channel names such as `constructor` / `__proto__` could break the plotter — hardened.
4. Live JSON log and exported JSON used different layouts — now byte-identical.
5. Rotation: far clock jumps were slow; idle files were finalised only at the next record — O(1) and timer-based close.
6. Self-test sent the token on the "must be denied" check (caught only by the packaged run); the launcher client would also have gone through a corporate proxy — rewritten (`http.client`).
7. Data folder moved from roaming `%APPDATA%` to local `%LOCALAPPDATA%` (folder redirection / DLP scanning of big logs); read-only folder now falls back to temp with a warning.
8. Validation errors returned HTTP 200; unknown/odd port names (e.g. `socket://`) accepted — now 400/409 with an allow-list.
9. Self-test used `assert` (silently skipped under `python -O`); page counter formatting.
10. *Found by the first real CI run:* the Windows-only version-resource test read PyInstaller attributes that don't exist (it packs versions into `fileVersionMS/LS`) — test fixed against PyInstaller's source.
11. *Found by the first real CI run:* the locked `jsdom` 31 needs Node >= 22 but CI used Node 20 — pinned `jsdom` 26 (Node >= 18); browser tests now verified on **Node 18, 20 and 22**, CI uses Node 22. Added `.gitattributes` so Windows checkouts don't rewrite line endings of sample logs.

## NOT tested here — please verify on your PCs
* Your actual **DLP product, GPO, AppLocker/WDAC and SmartScreen** policies (the installer is unsigned).
* **Native window rendering, installer silent-install/uninstall and the packaged exe on real Windows** — covered by CI steps (GUI smoke test is *advisory* on the first run; installer lifecycle is mandatory) but I could not execute them myself.
* **Real USB-serial hardware/drivers**, Windows COM-port quirks, high-DPI scaling, Windows-only `os.startfile`/Explorer button.
* Local timestamps in CSV/TXT at the DST "fall-back" hour are ambiguous (JSON/BIN carry epoch time and are exact).

Re-run everything: `run_tests.bat` (Windows) or `./run_tests.sh`.
