#!/usr/bin/env python3
"""
Serial Monitor - desktop launcher.

Starts the local backend on 127.0.0.1 (random access token, loopback only, no outbound network) and shows the UI
in a native window (pywebview / Microsoft Edge WebView2). If no native web view is available it falls back to your
default browser plus a small control window.

  python app.py                  native window
  python app.py --browser        use the default browser instead
  python app.py --selftest       headless end-to-end check (UI, auth, ports, simulator, time-split log file)
  python app.py --gui-selftest   opens the native window, drives the UI via JavaScript, closes it (CI on Windows)
  python app.py --data-dir DIR   settings + app.log + default log folder
Default data dir: %LOCALAPPDATA%\\SerialMonitor (Windows, local - not roamed/redirected), ~/.local/share/SerialMonitor (Linux)
"""
import argparse, http.client, json, os, secrets, socket, sys, tempfile, threading, time, traceback, webbrowser

import server

APP_NAME = "Serial Monitor"
PREFERRED_PORT = 47653        # fixed so the page origin (and its saved quick commands/theme) stays stable


def default_data_dir():
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "SerialMonitor")


def writable_dir(path):
    """Return (dir, warning). Falls back to a temp folder if the preferred one is read-only (locked-down PCs)."""
    try:
        server.check_writable(path)
        return path, ""
    except OSError as e:
        fb = os.path.join(tempfile.gettempdir(), "SerialMonitor")
        try:
            server.check_writable(fb)
        except OSError:
            raise SystemExit("No writable folder available for logs/settings: %s" % e)
        return fb, "Data folder %s is not writable (%s); using %s" % (path, e.__class__.__name__, fb)


def pick_port():
    for port in (PREFERRED_PORT, 0):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError("no free TCP port")


class Api:
    """Tiny loopback client. http.client never uses HTTP(S)_PROXY, so corporate proxy settings cannot break it."""
    def __init__(self, port, token):
        self.port, self.token = port, token

    def call(self, method, path, body=None, token=True, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token: h["X-SM-Token"] = self.token
        if body is not None: h["Content-Type"] = "application/json"
        h.update(headers or {})
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse(); data = r.read(); c.close()
        return r.status, data


def need(cond, msg):
    """Like assert, but survives python -O (a self-test must never silently pass)."""
    if not cond:
        raise RuntimeError(msg)


def selftest(port, token, out_file):
    """Exercise the packaged app end to end: UI, access control, ports, simulator, time-split log file."""
    api = Api(port, token)
    try:
        st, html = api.call("GET", "/", token=False)
        need(st == 403, "UI must require the access token")
        st, html = api.call("GET", "/", token=False, headers={"Cookie": "sm_tok_%d=%s" % (port, token)})
        need(st == 200 and b"Serial Monitor" in html, "frontend not served")
        need(api.call("GET", "/api/status", token=False)[0] == 401, "API must require the token")
        need(api.call("GET", "/api/status", headers={"Host": "evil.example"})[0] == 403, "bad Host accepted")
        need(any(p["dev"] == "SIMULATOR" for p in json.loads(api.call("GET", "/api/ports")[1])), "no simulator port")
        st, body = api.call("POST", "/api/connect", {"port": "SIMULATOR", "baud": 115200, "mode": "text", "fmt": "csv", "split": 1})
        need(st == 200 and json.loads(body).get("ok"), body)
        time.sleep(1.5)
        s = json.loads(api.call("GET", "/api/status")[1])
        need(s["bytes"] > 0, "no data received")
        path = s["logpath"]
        api.call("POST", "/api/disconnect", {})
        need(path and os.path.getsize(path) > 200, "log file missing")
        msg, code = "SELFTEST OK: log %s (%d bytes)" % (os.path.basename(path), os.path.getsize(path)), 0
    except Exception as e:                                   # noqa
        msg, code = "SELFTEST FAILED: %r" % e, 1
    with open(out_file, "w") as f:
        f.write(msg + "\n")
    print(msg)
    return code


def gui_selftest(url, out_file):
    """Open the real native window, click Connect (simulator) via JavaScript, verify rows/plot/logo, close."""
    import webview
    res = {"msg": "GUI SELFTEST FAILED: window never ran", "code": 1}

    def js(w, expr): return w.evaluate_js(expr)

    def check(w):
        try:
            for _ in range(150):
                if js(w, "document.readyState==='complete'&&!!document.querySelector('#conn')"): break
                time.sleep(0.2)
            else: raise RuntimeError("page did not load")
            need(js(w, "document.querySelector('.badge img').naturalWidth>0"), "logo not rendered")
            js(w, "document.querySelector('#conn').click()")
            for _ in range(60):
                if (js(w, "document.querySelectorAll('#tb tr').length") or 0) > 3: break
                time.sleep(0.2)
            else: raise RuntimeError("no rows after connect")
            need((js(w, "Object.keys(ch).length") or 0) >= 2, "plot channels not created")
            need(js(w, "document.querySelector('#cv').width>0"), "plot canvas empty")
            js(w, "document.querySelector('#conn').click()")
            res.update(msg="GUI SELFTEST OK: window, logo, table, plot all working", code=0)
        except Exception as e:                               # noqa
            res.update(msg="GUI SELFTEST FAILED: %r" % e, code=1)
        finally:
            w.destroy()

    w = webview.create_window(APP_NAME, url, width=1200, height=800)
    kw = {"gui": "edgechromium"} if sys.platform == "win32" else {}
    webview.start(check, w, **kw)
    with open(out_file, "w") as f:
        f.write(res["msg"] + "\n")
    print(res["msg"])
    return res["code"]


def fallback_window(url):
    """Used when no native web view exists: open the browser and keep a tiny control window."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk(); root.title(APP_NAME); root.resizable(False, False)
        tk.Label(root, text="%s is running.\nIt was opened in your default browser.\n\nClose this window to quit." % APP_NAME,
                 padx=24, pady=16).pack()
        tk.Button(root, text="Open in browser", command=lambda: webbrowser.open(url)).pack(pady=(0, 14))
        root.mainloop()
    except Exception:
        threading.Event().wait()


def run_window(url, data_dir):
    import webview                                            # lazy: not needed for --selftest/--browser
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True            # Export buttons -> save dialog
    except Exception:
        pass  # nosec B110 - best-effort cleanup, failure is harmless
    webview.create_window(APP_NAME, url, width=1320, height=860, min_size=(980, 600))
    kw = dict(private_mode=False, storage_path=os.path.join(data_dir, "webview"))
    if sys.platform == "win32":
        kw["gui"] = "edgechromium"                            # never fall back to the old IE engine
    webview.start(**kw)


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--gui-selftest", action="store_true")
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--data-dir")
    a = ap.parse_args()

    data_dir, warn = writable_dir(a.data_dir or default_data_dir())
    port, token = pick_port(), secrets.token_urlsafe(24)
    srv, mon = server.serve("127.0.0.1", port, os.path.join(data_dir, "logs"),
                            os.path.join(data_dir, "settings.json"), token, warn)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/?k=%s" % (port, token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    code = 0
    try:
        if a.selftest:
            code = selftest(port, token, os.path.join(data_dir, "selftest.txt"))
        elif a.gui_selftest:
            code = gui_selftest(url, os.path.join(data_dir, "gui_selftest.txt"))
        elif a.browser:
            fallback_window(url)
        else:
            try:
                run_window(url, data_dir)
            except Exception:
                with open(os.path.join(data_dir, "app.log"), "a") as f:
                    f.write("%s native window failed:\n%s\n" % (time.ctime(), traceback.format_exc()))
                fallback_window(url)
    finally:
        mon.disconnect()                                      # flush + close log files
        srv.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
