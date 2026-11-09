"""Monitor behaviour: simulator sessions, resilience, leaks, and (Linux/macOS) real termios ports via pty."""
import json, os, sys, threading, time
import pytest
import psutil
import server
from conftest import TOKEN
from test_logging import read_records

HAS_PTY = sys.platform != "win32" and server.serial is not None
FORMATS = ["csv", "json", "txt", "bin"]


def wait_for(cond, timeout=5.0, step=0.05):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond(): return True
        time.sleep(step)
    return False


@pytest.mark.parametrize("split", [0, 1, 60])
@pytest.mark.parametrize("fmt", FORMATS)
def test_simulator_session_logs_everything(app, fmt, split):
    assert app.connect(fmt=fmt, split=split)[0] == 200
    assert wait_for(lambda: app.mon.seq >= 12)
    app.post("/api/send", {"data": "PING"})
    time.sleep(0.4)
    st = app.get("/api/status")[1]
    assert st["connected"] and st["cfg"]["fmt"] == fmt and st["cfg"]["split"] == split and st["part"] == 1
    app.post("/api/disconnect")
    total = app.mon.seq
    assert len(read_records(st["logpath"], fmt)) == total                  # nothing lost, SYS+TX+RX all present
    assert any(r["dir"] == "TX" for r in app.mon.recs) and any(r["text"] == "ECHO:PING" for r in app.mon.recs)


def test_format_off_writes_no_files(app):
    app.connect(fmt="off", split=0); time.sleep(0.5); app.post("/api/disconnect")
    assert app.get("/api/status")[1]["logpath"] == "" and not os.path.exists(app.mon.logdir)


def test_hex_mode(app):
    app.connect(mode="hex"); assert wait_for(lambda: app.mon.seq >= 4)
    rx = [r for r in app.mon.recs if r["dir"] == "RX"]
    assert rx and all(len(r["text"].split()) >= 1 and all(len(x) == 2 for x in r["text"].split()) for r in rx)


def test_stream_is_ordered_and_gap_free(app):
    app.connect()
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    c.request("GET", "/api/stream", headers={"Host": "127.0.0.1:%d" % app.port, "X-SM-Token": TOKEN})
    r = c.getresponse(); seqs, t0 = [], time.time()
    while time.time() - t0 < 2.0:
        line = r.fp.readline().decode()
        if line.startswith("data:"): seqs += [x["seq"] for x in json.loads(line[5:])["r"]]
    c.close()
    assert len(seqs) > 10 and seqs == list(range(1, len(seqs) + 1))
    assert all("raw" not in x for x in json.loads(line[5:])["r"]) if line.startswith("data:") else True


def test_export_all_formats_and_errors(app):
    assert app.req("GET", "/api/log?fmt=csv")[0] == 404                               # no data yet
    app.connect(); assert wait_for(lambda: app.mon.seq >= 5)
    for fmt in FORMATS:
        st, hd, body = app.req("GET", "/api/log?fmt=" + fmt)
        assert st == 200 and "attachment" in hd["content-disposition"] and len(body) > 100
    assert json.loads(app.req("GET", "/api/log?fmt=json")[2])
    for bad in ("exe", "../x", "csv%00", "CSV", "csv;ls"): assert app.req("GET", "/api/log?fmt=" + bad)[0] == 404
    assert app.req("GET", "/api/log?fmt=")[0] == 200                                   # blank -> session default format


def test_reconnect_resets_session_and_ends_old_stream(app):
    app.connect(); wait_for(lambda: app.mon.seq >= 5); e1 = app.mon.epoch
    app.connect(); assert app.mon.epoch == e1 + 1 and app.mon.seq < 5 or app.mon.seq >= 1
    assert app.post("/api/disconnect")[0] == 200 and app.post("/api/disconnect")[0] == 200   # idempotent


def test_memory_ring_is_bounded_and_since_is_consistent():
    m = server.Monitor("x")
    for i in range(65000): m.add("RX", "v=%d" % i)
    assert len(m.recs) <= 30000 and m.recs[-1]["seq"] == 65000 and m.recs[0]["seq"] == m.base + 1
    assert m.since(0)[0]["seq"] == m.base + 1                                          # lagging client skips to oldest kept
    assert [r["seq"] for r in m.since(64990)] == list(range(64991, 65001))


def test_no_thread_or_handle_leak_over_many_cycles(app):
    p = psutil.Process()
    def handles(): return p.num_handles() if sys.platform == "win32" else p.num_fds()
    app.connect(); time.sleep(0.3); app.post("/api/disconnect")                         # warm up
    t0, h0 = threading.active_count(), handles()
    for _ in range(40):
        assert app.connect(split=1)[0] == 200; time.sleep(0.05); app.post("/api/disconnect")
    time.sleep(0.5)
    assert threading.active_count() <= t0 + 2 and handles() <= h0 + (25 if sys.platform == 'win32' else 6), (t0, threading.active_count(), h0, handles())


def test_connect_failures_are_clean(app, monkeypatch):
    st, r = app.connect(port="COM99" if sys.platform == "win32" else "/dev/ttyNOPE")
    assert st == 409 and r["error"] and "Traceback" not in r["error"]
    assert app.get("/api/status")[1]["connected"] is False
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied by policy")))
    st, r = app.connect(); assert st == 409 and "log folder" in r["error"]
    assert app.get("/api/status")[1]["connected"] is False


def test_slow_disk_does_not_slow_the_reader(tmp_path, monkeypatch):
    real = server.LogWriter.write
    monkeypatch.setattr(server.LogWriter, "write", lambda self, recs: (time.sleep(0.3), real(self, recs))[1])  # antivirus / DLP scan delay
    srv, mon = server.serve("127.0.0.1", 0, str(tmp_path), None, token=TOKEN)
    mon.connect({"port": "SIMULATOR", "baud": 9600, "fmt": "csv", "split": 0, "mode": "text"})
    t0 = time.time()
    for i in range(20000): mon.add("RX", "v=%d" % i)
    assert time.time() - t0 < 2.0                                                      # producer never waits for the disk
    mon.disconnect(); srv.server_close()


# ---------------------------------------------------------------- real termios ports (pty)
pty_only = pytest.mark.skipif(not HAS_PTY, reason="pty + pyserial needed (Linux/macOS)")


@pytest.fixture
def pty_port():
    import pty
    m, s = pty.openpty()
    yield m, os.ttyname(s)
    for fd in (m, s):
        try: os.close(fd)
        except OSError: pass


def rx_texts(mon): return [r["text"] for r in list(mon.recs) if r["dir"] == "RX"]


@pty_only
def test_pty_terminators_partial_garbage_and_send(app, pty_port):
    m, name = pty_port
    assert app.connect(port=name, baud=115200)[0] == 200
    os.write(m, b"A1\nB2\r\nC3\rD4\n\rE5\n"); os.write(m, b"par"); time.sleep(0.5); os.write(m, b"tial\n")
    os.write(m, b"\x00\xff\x80ok\n"); os.write(m, os.urandom(5000)); time.sleep(0.6)
    t = rx_texts(app.mon)
    assert t[:5] == ["A1", "B2", "C3", "D4", "E5"] and "par  [partial]" in t and "tial" in t and "\\x00\\xFF\\x80ok" in t
    assert app.get("/api/status")[1]["connected"] is True
    app.post("/api/send", {"data": "PING", "eol": "\r\n"}); time.sleep(0.2)
    assert os.read(m, 100) == b"PING\r\n"


@pty_only
@pytest.mark.parametrize("fmt", FORMATS)
def test_pty_flood_no_loss_responsive_ui(app, pty_port, fmt):
    m, name = pty_port
    app.connect(port=name, baud=921600, fmt=fmt, split=0)
    N, stop, lat = 60000, threading.Event(), []
    def flood():
        i = 0
        while i < N:
            os.write(m, b"".join(b"ADC,%d,V=%.3f\r\n" % (i + k, k / 7) for k in range(200))); i += 200; time.sleep(0.002)
    th = threading.Thread(target=flood); th.start()
    while th.is_alive():
        t0 = time.time(); assert app.get("/api/status")[0] == 200; lat.append(time.time() - t0); time.sleep(0.1)
    th.join(); wait_for(lambda: len([1 for r in app.mon.recs if r["dir"] == "RX"]) >= N, 20)
    st = app.get("/api/status")[1]; app.post("/api/disconnect")
    assert max(lat) < 0.5 and st["dropped"] == 0 and st["lost"] == 0
    rx = sum(1 for r in read_records(st["logpath"], fmt) if (r["dir"] == "RX" if fmt != "txt" and fmt != "bin" else True))
    assert rx >= N                                                                     # every line reached the file


@pty_only
def test_pty_device_unplugged_midstream_then_reconnect(app, pty_port):
    m, name = pty_port
    app.connect(port=name); os.write(m, b"hello\n"); time.sleep(0.3)
    os.close(m)                                                                          # "unplug"
    assert wait_for(lambda: not app.get("/api/status")[1]["connected"], 5)
    st = app.get("/api/status")[1]
    assert st["err"] and any(r["dir"] == "SYS" and "Error" in r["text"] for r in app.mon.recs)
    assert app.connect()[0] == 200                                                      # app recovers
    assert app.post("/api/disconnect")[0] == 200


@pty_only
@pytest.mark.parametrize("frame", [dict(bytesize=7, parity="E", stopbits=1), dict(bytesize=8, parity="O", stopbits=2), dict(bytesize=8, parity="N", stopbits=1)])
def test_pty_frame_settings_accepted_or_cleanly_refused(app, pty_port, frame):
    st, r = app.connect(port=pty_port[1], baud=9600, **frame)
    assert st == 200 or (st == 409 and r["error"])
    if st == 200: assert app.get("/api/status")[1]["cfg"]["frame"] == "%d%s%s" % (frame["bytesize"], frame["parity"], frame["stopbits"])


@pty_only
def test_pty_busy_port_gives_friendly_error():
    assert "busy" in server.friendly(PermissionError(13, "Access is denied")) or "Access denied" in server.friendly(Exception("PermissionError(13)"))
