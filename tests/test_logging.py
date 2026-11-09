"""Log writers: every format, all 12 split intervals, rotation edge cases, failure handling."""
import csv, json, os, struct, time
import pytest
import server
import make_samples as ms

START = time.mktime((2026, 11, 8, 10, 0, 0, 0, 0, -1))
FORMATS = ["csv", "json", "txt", "bin"]


def read_records(path, fmt):
    if fmt == "json": return json.load(open(path, encoding="utf-8"))
    if fmt == "csv":
        rows = list(csv.reader(open(path, newline="", encoding="utf-8")))
        assert rows[0] == ["seq", "timestamp", "delta_s", "dir", "data"]
        return [{"seq": int(r[0]), "ts": r[1], "dir": r[3], "text": r[4]} for r in rows[1:]]
    if fmt == "txt": return [l for l in open(path, encoding="utf-8").read().splitlines()]
    b = open(path, "rb").read(); assert b[:5] == server.MAGIC
    out, o = [], 5
    while o < len(b):
        t, d, dr, ln = struct.unpack("<dIBH", b[o:o + 15]); o += 15
        out.append({"t": t, "dir": dr, "raw": b[o:o + ln]}); o += ln
    return out


def part_times(path, fmt):
    """epoch seconds of the first/last record of one part file"""
    if fmt == "json":
        a = read_records(path, fmt); return a[0]["t"], a[-1]["t"]
    if fmt == "bin":
        a = read_records(path, fmt); return a[0]["t"], a[-1]["t"]
    from datetime import datetime
    ts = [r["ts"] if fmt == "csv" else r.split()[1] + " " + r.split()[2] for r in read_records(path, fmt)]
    p = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    return p(ts[0]), p(ts[-1])


@pytest.mark.parametrize("minutes", [m for m in server.SPLITS if m])
@pytest.mark.parametrize("fmt", FORMATS)
def test_rotation_every_interval_every_format(tmp_path, minutes, fmt):
    span = minutes * 60
    recs = ms.make(START, int(span * 2.5), hz=1)
    rw = server.RotatingWriter(str(tmp_path), "s", fmt, minutes)
    for i in range(0, len(recs), 997): rw.write(recs[i:i + 997])            # odd batch size, like the writer thread
    rw.close()
    expected_parts = len({int((r["t"] - START) // span) for r in recs})
    files = sorted(os.listdir(tmp_path))
    assert files == ["s_part%03d%s" % (i + 1, server.EXT[fmt]) for i in range(expected_parts)] and expected_parts == 3
    total, last_end = 0, 0
    for k, f in enumerate(files):
        p = os.path.join(tmp_path, f); n = len(read_records(p, fmt)); total += n
        first, last = part_times(p, fmt)
        assert START + k * span - 0.002 <= first and last < START + (k + 1) * span + 0.002, (f, first - START, last - START)
        assert first >= last_end - 0.002; last_end = last           # parts are ordered, non-overlapping
    assert total == len(recs)


def test_single_file_mode(tmp_path):
    rw = server.RotatingWriter(str(tmp_path), "s", "csv", 0)
    rw.write(ms.make(START, 7200, 1)); rw.close()
    assert os.listdir(tmp_path) == ["s_part001.csv"]


def test_files_created_lazily_no_empty_parts(tmp_path):
    rw = server.RotatingWriter(str(tmp_path), "s", "csv", 1)
    assert os.listdir(tmp_path) == []
    rw.write(ms.make(START, 5, 1)); rw.close()
    assert len(os.listdir(tmp_path)) == 1


def test_idle_tick_finalises_file_on_time_and_skips_empty_intervals(tmp_path):
    rw = server.RotatingWriter(str(tmp_path), "s", "csv", 1)
    rw.write(ms.make(START, 10, 1))
    rw.tick(START + 30); assert rw.lw is not None                    # not due yet
    first = rw.paths[0]
    rw.tick(START + 61); assert rw.lw is None                        # closed at the boundary, no data needed
    assert len(read_records(first, "csv")) == 11
    rw.tick(START + 5000)                                            # long idle: must not create 80 empty files
    rw.write(ms.make(START + 5001, 3, 1)); rw.close()
    assert len(os.listdir(tmp_path)) == 2


def test_clock_jumps_backwards_and_far_forwards(tmp_path):
    rw = server.RotatingWriter(str(tmp_path), "s", "csv", 1)
    a = ms.make(START, 30, 1); b = ms.make(START - 3600, 30, 1)      # NTP / DST step back one hour
    c = ms.make(START + 100 * 86400, 5, 1)                            # 100 days forward
    t0 = time.time()
    rw.write(a + b + c); rw.close()
    assert time.time() - t0 < 2.0
    assert len(os.listdir(tmp_path)) == 2                             # backwards jump stays in the same part
    assert sum(len(read_records(os.path.join(tmp_path, f), "csv")) for f in os.listdir(tmp_path)) == len(a + b + c)


@pytest.mark.parametrize("fmt", FORMATS)
def test_writer_equals_build(tmp_path, fmt):
    recs = ms.make(START, 20, 5)
    w = server.LogWriter(str(tmp_path / "x"), fmt)
    w.write(recs[:50]); w.write(recs[50:]); w.close()
    assert open(tmp_path / "x", "rb").read() == server.build(fmt, recs)


def test_json_is_valid_after_every_batch(tmp_path):
    w = server.LogWriter(str(tmp_path / "x.json"), "json")
    assert json.load(open(tmp_path / "x.json")) == []
    recs = ms.make(START, 10, 5)
    for i in range(0, len(recs), 7):
        w.write(recs[i:i + 7]); assert len(json.load(open(tmp_path / "x.json", encoding="utf-8"))) == min(len(recs), i + 7)
    w.close()


def test_csv_special_characters_roundtrip(tmp_path):
    texts = ['a,b', 'say "hi"', "semi;colon", "tab\there", "uni\\xC3\\xA9", "=cmd|' /C calc'!A0", "  spaced  "]
    recs = [ms.rec(i + 1, START + i, START + i - 1, "RX", t) for i, t in enumerate(texts)]
    w = server.LogWriter(str(tmp_path / "x.csv"), "csv"); w.write(recs); w.close()
    assert [r["text"] for r in read_records(str(tmp_path / "x.csv"), "csv")] == texts


def test_bin_roundtrip_and_decoder(tmp_path, capsys):
    recs = ms.make(START, 5, 4)
    recs.append({**ms.rec(len(recs) + 1, START + 6, START + 5, "RX", "x"), "raw": bytes(range(256))})
    w = server.LogWriter(str(tmp_path / "x.bin"), "bin"); w.write(recs); w.close()
    assert [r["raw"] for r in read_records(str(tmp_path / "x.bin"), "bin")][-1] == bytes(range(256))
    server.decode_bin(str(tmp_path / "x.bin"))
    assert len(capsys.readouterr().out.strip().splitlines()) == len(recs)


def test_clean_never_outputs_control_chars():
    out = server.clean(bytes(range(256)))
    assert all(32 <= ord(c) < 127 for c in out) and "\\x00" in out and "\\xFF" in out and "A" in out


def test_writer_errors_are_reported_and_never_block(tmp_path, monkeypatch):
    rw = server.RotatingWriter(str(tmp_path), "s", "csv", 0)
    sink = server.Sink(rw); sink.start()
    real = server.LogWriter.write; state = {"fail": True}
    def flaky(self, recs):
        if state["fail"]: raise OSError(28, "No space left on device")
        return real(self, recs)
    monkeypatch.setattr(server.LogWriter, "write", flaky)
    recs = ms.make(START, 10, 1)
    for r in recs[:5]: sink.put(r)
    time.sleep(0.8)
    assert "No space" in sink.error and sink.lost == 5
    state["fail"] = False
    for r in recs[5:]: sink.put(r)
    time.sleep(0.8); t0 = time.time(); sink.finish()
    assert time.time() - t0 < 5
    assert len(read_records(rw.paths[0], "csv")) == len(recs) - 5


def test_unicode_and_spaced_paths(app):
    d = app.tmp / "Üser Ñame" / "My Logs (test)"
    assert app.post("/api/logdir", {"path": str(d)})[0] == 200
    assert app.connect(split=1)[0] == 200
    time.sleep(0.6); st = app.get("/api/status")[1]; app.post("/api/disconnect")
    assert st["logpath"].startswith(str(d)) and os.path.getsize(st["logpath"]) > 100


def test_two_sessions_in_same_second_never_overwrite(app):
    for _ in range(3):
        assert app.connect()[0] == 200; time.sleep(0.3); app.post("/api/disconnect")
    folders = os.listdir(app.mon.logdir)
    assert len(folders) == 3
    for f in folders: assert os.path.getsize(os.path.join(app.mon.logdir, f, f + "_part001.csv")) > 100
