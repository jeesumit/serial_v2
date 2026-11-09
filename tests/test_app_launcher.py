"""Desktop launcher: data folders, fallback, port choice, self-test (incl. corporate proxy variables)."""
import os, socket, subprocess, sys
import pytest
import app as launcher
import server
from conftest import ROOT, TOKEN


def test_default_data_dir_is_local_not_roaming_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local")); monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    d = launcher.default_data_dir()
    assert d == os.path.join(str(tmp_path / "Local"), "SerialMonitor") and "Roaming" not in d


def test_unwritable_data_dir_falls_back_to_temp_with_warning(monkeypatch, tmp_path):
    real = server.check_writable
    def fake(p):
        if "locked" in p: raise PermissionError(13, "denied by policy")
        return real(p)
    monkeypatch.setattr(server, "check_writable", fake)
    d, warn = launcher.writable_dir(str(tmp_path / "locked" / "data"))
    assert d.endswith("SerialMonitor") and "not writable" in warn and "PermissionError" in warn
    ok, warn = launcher.writable_dir(str(tmp_path / "fine")); assert ok == str(tmp_path / "fine") and warn == ""


def test_nothing_writable_is_a_clear_exit(monkeypatch):
    monkeypatch.setattr(server, "check_writable", lambda p: (_ for _ in ()).throw(OSError("read-only")))
    with pytest.raises(SystemExit) as e: launcher.writable_dir("/x")
    assert "No writable folder" in str(e.value)


def test_pick_port_prefers_fixed_then_falls_back():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", launcher.PREFERRED_PORT)); s.listen(1)      # occupy the preferred port ourselves
    except OSError:
        pass                                                              # already occupied by another instance: same situation
    try:
        other = launcher.pick_port()
        assert other != launcher.PREFERRED_PORT and other > 0              # must fall back to a free random port
    finally:
        s.close()
    probe = socket.socket()
    try: probe.bind(("127.0.0.1", launcher.PREFERRED_PORT)); free = True
    except OSError: free = False
    finally: probe.close()
    if free: assert launcher.pick_port() == launcher.PREFERRED_PORT         # and use the fixed port when it is available


def test_selftest_passes_against_a_live_server_and_fails_with_wrong_token(app, tmp_path):
    assert launcher.selftest(app.port, TOKEN, str(tmp_path / "ok.txt")) == 0 and "SELFTEST OK" in (tmp_path / "ok.txt").read_text()
    assert launcher.selftest(app.port, "w" * 32, str(tmp_path / "bad.txt")) == 1 and "FAILED" in (tmp_path / "bad.txt").read_text()


def test_loopback_client_ignores_proxy_environment(app, monkeypatch):
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"): monkeypatch.setenv(k, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    st, body = launcher.Api(app.port, TOKEN).call("GET", "/api/status")
    assert st == 200


def test_full_launcher_selftest_in_a_subprocess_with_proxy_vars(tmp_path):
    env = {**os.environ, "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "app.py"), "--selftest", "--data-dir", str(tmp_path / "d")],
                       capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0 and "SELFTEST OK" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "d" / "logs").is_dir()
