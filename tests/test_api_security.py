"""Access control, input validation, HTTP hardening, concurrency. (Corporate / DLP review checklist.)"""
import http.client, json, os, socket, subprocess, sys, threading, time
import pytest
import server
from conftest import TOKEN, ROOT

GET_ROUTES = ["/api/ports", "/api/status", "/api/log", "/api/stream"]
POST_ROUTES = ["/api/connect", "/api/disconnect", "/api/send", "/api/open-folder", "/api/logdir"]


def test_ui_requires_token_and_cookie_flow(app):
    assert app.req("GET", "/", token=False)[0] == 403
    st, hd, _ = app.req("GET", "/?k=" + TOKEN, token=False)
    assert st == 302 and hd["location"] == "/"
    ck = hd["set-cookie"]
    assert "HttpOnly" in ck and "SameSite=Strict" in ck and "Path=/" in ck and ("sm_tok_%d=" % app.port) in ck
    st, hd, body = app.req("GET", "/", token=False, headers={"Cookie": "sm_tok_%d=%s" % (app.port, TOKEN)})
    assert st == 200 and b"Serial Monitor" in body and b"<script>" in body
    assert app.req("GET", "/?k=wrong", token=False)[0] == 403


@pytest.mark.parametrize("path", GET_ROUTES)
def test_every_get_route_needs_token(app, path):
    assert app.req("GET", path, token=False)[0] == 401
    assert app.req("GET", path, headers={"X-SM-Token": "x" * 32})[0] == 401
    assert app.req("GET", path, token=False, headers={"Cookie": "sm_tok_%d=nope" % app.port})[0] == 401


@pytest.mark.parametrize("path", POST_ROUTES)
def test_every_post_route_needs_token(app, path):
    assert app.req("POST", path, {}, token=False)[0] == 401


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1", "127.0.0.1.evil.com:80", "localhost.evil.com", "0.0.0.0:1", ""])
def test_dns_rebinding_host_header_rejected(app, host):
    assert app.req("GET", "/api/status", headers={"Host": host})[0] == 403
    assert app.req("GET", "/", token=False, headers={"Host": host})[0] == 403


@pytest.mark.parametrize("origin", ["http://evil.example", "https://127.0.0.1:%d", "http://127.0.0.1:1", "null"])
def test_cross_site_origin_rejected_even_with_valid_token(app, origin):
    o = origin % app.port if "%d" in origin else origin
    assert app.req("POST", "/api/disconnect", {}, headers={"Origin": o})[0] == 403
    assert app.req("GET", "/api/status", headers={"Origin": o})[0] == 403


def test_same_origin_post_allowed(app):
    assert app.req("POST", "/api/disconnect", {}, headers={"Origin": "http://127.0.0.1:%d" % app.port})[0] == 200


def test_post_must_be_json(app):
    assert app.req("POST", "/api/disconnect", raw=b"{}", ctype="text/plain")[0] == 415
    assert app.req("POST", "/api/disconnect", raw=b"a=b", ctype="application/x-www-form-urlencoded")[0] == 415


def test_security_headers_everywhere(app):
    for st, hd, _ in (app.req("GET", "/", token=False, headers={"Cookie": "sm_tok_%d=%s" % (app.port, TOKEN)}),
                      app.req("GET", "/api/status"), app.req("GET", "/nope"), app.req("GET", "/api/status", token=False)):
        csp = hd["content-security-policy"]
        assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert hd["x-content-type-options"] == "nosniff" and hd["cache-control"] == "no-store" and hd["x-frame-options"] == "DENY"
        assert hd["referrer-policy"] == "no-referrer" and "python" not in hd.get("server", "").lower()
        assert "access-control-allow-origin" not in hd                      # no CORS: cross-origin pages can't read replies


def test_no_external_resources_in_frontend():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    import re
    assert not re.findall(r"""(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", html)
    assert not re.findall(r"""(?:fetch|XMLHttpRequest|WebSocket|EventSource|import)\s*\(?\s*["']https?://""", html)
    assert "eval(" not in html and "new Function" not in html and "document.write" not in html


def test_oversized_body_rejected(app):
    try:
        st, _, _ = app.req("POST", "/api/connect", raw=b"{" + b" " * 70000 + b"}")
        assert st == 413
    except (ConnectionError, BrokenPipeError):
        pass
    assert app.get("/api/status")[0] == 200


@pytest.mark.parametrize("raw", [b"{", b"not json", b"[1,2]", b'"str"', b"null", b"\xff\xfe\x00", b"{" * 5000])
def test_malformed_json_gives_clean_400(app, raw):
    st, hd, body = app.req("POST", "/api/connect", raw=raw)
    assert st == 400 and "error" in json.loads(body) and b"Traceback" not in body


BAD_CONNECT = [
    {}, {"port": ""}, {"port": None}, {"port": 5}, {"port": "socket://10.0.0.5:23"}, {"port": "rfc2217://h:1"},
    {"port": "../../etc/passwd"}, {"port": "/dev/../etc/passwd"}, {"port": "COM"}, {"port": "COM1234"}, {"port": "COM1; calc"},
    {"port": "x" * 500}, {"port": "\\\\server\\share"}, {"port": "SIMULATOR", "baud": 0}, {"port": "SIMULATOR", "baud": -9600},
    {"port": "SIMULATOR", "baud": 49}, {"port": "SIMULATOR", "baud": 4000001}, {"port": "SIMULATOR", "baud": 9600.5},
    {"port": "SIMULATOR", "baud": "abc"}, {"port": "SIMULATOR", "baud": None}, {"port": "SIMULATOR", "baud": True},
    {"port": "SIMULATOR", "baud": [9600]}, {"port": "SIMULATOR", "baud": 1e300}, {"port": "SIMULATOR", "baud": 9600, "bytesize": 9},
    {"port": "SIMULATOR", "baud": 9600, "bytesize": "x"}, {"port": "SIMULATOR", "baud": 9600, "parity": "X"},
    {"port": "SIMULATOR", "baud": 9600, "stopbits": 3}, {"port": "SIMULATOR", "baud": 9600, "mode": "binary"},
    {"port": "SIMULATOR", "baud": 9600, "fmt": "xls"}, {"port": "SIMULATOR", "baud": 9600, "fmt": "../x"},
    {"port": "SIMULATOR", "baud": 9600, "split": 7}, {"port": "SIMULATOR", "baud": 9600, "split": -1},
    {"port": "SIMULATOR", "baud": 9600, "split": True}, {"port": "SIMULATOR", "baud": 9600, "split": "60"},
]


@pytest.mark.parametrize("body", BAD_CONNECT, ids=range(len(BAD_CONNECT)))
def test_connect_validation_rejects_and_server_stays_healthy(app, body):
    st, r = app.post("/api/connect", body)
    assert st == 400 and "error" in r
    s = app.get("/api/status")[1]
    assert s["connected"] is False


@pytest.mark.parametrize("body", [{"port": "SIMULATOR", "baud": 300}, {"port": "simulator", "baud": "9600"}, {"port": "SIMULATOR", "baud": 4000000},
                                  {"port": "SIMULATOR", "baud": 115200, "bytesize": "7", "parity": "E", "stopbits": 2},
                                  {"port": "SIMULATOR", "baud": 115200, "stopbits": 1.5}, {"port": "SIMULATOR", "baud": 9600.0, "fmt": "off", "split": 0}])
def test_valid_connect_variants_accepted(app, body):
    assert app.post("/api/connect", body)[0] == 200
    assert app.get("/api/status")[1]["connected"] is True


def test_unknown_methods_and_paths(app):
    for m in ("PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE"):
        assert app.req(m, "/api/status")[0] in (400, 405, 501), m
    assert app.get("/nope")[0] == 404 and app.post("/api/nope")[0] == 404


@pytest.mark.parametrize("path", ["/../server.py", "/static/../server.py", "/%2e%2e/server.py", "//etc/passwd", "/api/../server.py",
                                  "/static/index.html", "/index.html", "/..%2fserver.py", "/server.py"])
def test_path_traversal_never_serves_files(app, path):
    st, hd, body = app.req("GET", path)
    assert st in (400, 404) and b"def serve" not in body and b"Serial Monitor" not in body


def test_bound_to_loopback_only_and_timeout_set(app):
    assert app.srv.server_address[0] == "127.0.0.1" and server.Handler.timeout == 30


def test_cli_refuses_non_loopback_host():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "--host", "0.0.0.0", "--no-browser"],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode != 0 and "Refusing" in (r.stderr + r.stdout)


def test_concurrent_clients_and_abrupt_disconnects(app):
    app.connect()
    errs = []
    def hit():
        try:
            for _ in range(10):
                if app.get("/api/status")[0] != 200: errs.append("status")
        except Exception as e: errs.append(repr(e))
    ts = [threading.Thread(target=hit) for _ in range(30)]; [t.start() for t in ts]
    for _ in range(25):                                              # SSE clients that vanish mid-stream
        s = socket.create_connection(("127.0.0.1", app.port))
        s.sendall(("GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-SM-Token: %s\r\n\r\n" % (app.port, TOKEN)).encode())
        s.recv(200); s.close()
    [t.join() for t in ts]
    assert not errs and app.get("/api/status")[0] == 200


def test_slow_partial_request_does_not_block_others(app):
    s = socket.create_connection(("127.0.0.1", app.port)); s.sendall(b"GET /api/sta")
    t0 = time.time(); assert app.get("/api/status")[0] == 200 and time.time() - t0 < 1; s.close()


# ---- send / logdir endpoints
def test_send_validation(app):
    assert app.post("/api/send", {"data": "x"})[0] == 409                           # not connected
    app.connect()
    assert app.post("/api/send", {"data": "PING", "eol": "\r\n"})[0] == 200
    assert app.post("/api/send", {"data": "x", "eol": "; rm -rf /"})[0] == 400
    assert app.post("/api/send", {"data": "x" * 5000})[0] == 400
    assert app.post("/api/send", {"data": 123})[0] == 400


def test_logdir_validation_and_persistence(app, tmp_path):
    good = str(tmp_path / "approved logs")
    assert app.post("/api/logdir", {"path": good})[0] == 200 and os.path.isdir(good)
    assert json.load(open(app.tmp / "settings.json"))["logdir"] == good
    for bad in ["relative/path", "", None, 5, "x" * 300, ("Z:\\no_such_drive_xyz\\logs" if sys.platform == "win32" else "/dev/null/sub"), good + "\x00"]:
        assert app.post("/api/logdir", {"path": bad})[0] in (400, 409), bad
    (tmp_path / "afile").write_text("x")
    assert app.post("/api/logdir", {"path": str(tmp_path / "afile")})[0] == 409     # path is a file
    app.connect(); assert app.post("/api/logdir", {"path": good})[0] == 409          # not while logging
    app.post("/api/disconnect")
    srv2, mon2 = server.serve("127.0.0.1", 0, str(tmp_path / "other"), str(app.tmp / "settings.json"), token=TOKEN)
    assert mon2.logdir == good                                                         # remembered after restart
    srv2.server_close()


def test_unwritable_saved_logdir_falls_back_with_warning(tmp_path, monkeypatch):
    json.dump({"logdir": str(tmp_path / "gone")}, open(tmp_path / "s.json", "w"))
    monkeypatch.setattr(server, "check_writable", lambda p: (_ for _ in ()).throw(PermissionError("denied")))
    srv, mon = server.serve("127.0.0.1", 0, str(tmp_path / "def"), str(tmp_path / "s.json"), token=TOKEN)
    assert mon.logdir == str(tmp_path / "def") and "not writable" in mon.warn; srv.server_close()
    (tmp_path / "s2.json").write_text("{corrupt"); srv, mon = server.serve("127.0.0.1", 0, str(tmp_path / "d2"), str(tmp_path / "s2.json"), token=TOKEN)
    assert mon.logdir == str(tmp_path / "d2"); srv.server_close()


def test_open_folder_called_with_session_folder(app, monkeypatch):
    seen = []; monkeypatch.setattr(server, "open_folder", lambda p: seen.append(p))
    app.connect(); time.sleep(0.4); app.post("/api/open-folder")
    assert seen and os.path.dirname(app.get("/api/status")[1]["logpath"]) == seen[0]
