#!/usr/bin/env python3
"""
Serial Monitor - hardened local backend

Security model (for corporate / DLP environments)
  * Binds to 127.0.0.1 only (refuses anything else unless --allow-remote).
  * Per-launch random access token (cookie or X-SM-Token header) on every request; Host and Origin are
    checked (blocks DNS-rebinding and cross-site requests); POST must be application/json.
  * Strict Content-Security-Policy; no outbound network connections are ever made.
  * Port names are allow-listed (COMn, \\\\.\\COMn, /dev/..., SIMULATOR); all inputs are validated.
  * Writes only to the log folder (default %LOCALAPPDATA%\\SerialMonitor\\logs) + settings.json.

Pipeline: reader thread -> memory ring + bounded queue -> writer thread (batched, never blocks the reader).
Log files: one folder per session, split every N minutes (0 = single file; 1 5 10 15 20 30 40 45 50 60 90 120);
formats csv | json (always valid) | txt | bin | off.   bin = "SLG1\\x01" + records
<float64 epoch_s><uint32 delta_us><uint8 dir 0=RX 1=TX 2=SYS><uint16 len><raw bytes>
"""
import argparse, csv, hmac, io, json, math, os, queue, random, re, secrets, struct, sys, tempfile, threading, time, webbrowser
import subprocess  # nosec B404 - only used with fixed argv lists to open the log folder (no shell)
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None

SIM, MAGIC = "SIMULATOR", b"SLG1\x01"
EXT = {"csv": ".csv", "json": ".json", "txt": ".txt", "bin": ".bin"}
MIME = {"csv": "text/csv", "json": "application/json", "txt": "text/plain", "bin": "application/octet-stream"}
DIRS = {"RX": 0, "TX": 1, "SYS": 2}
CSV_HEAD = b"seq,timestamp,delta_s,dir,data\r\n"
SPLITS = (0, 1, 5, 10, 15, 20, 30, 40, 45, 50, 60, 90, 120)       # minutes; 0 = single file
PORT_RE = re.compile(r"^(?i:com\d{1,3}|\\\\\.\\com\d{1,3}|/dev/[A-Za-z0-9_./-]{1,64}|simulator)$")
MAX_BODY = 65536
SEC_HEADERS = {
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "X-Frame-Options": "DENY", "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "img-src data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def clean(buf): return "".join(chr(c) if 32 <= c < 127 else "\\x%02X" % c for c in buf)
def fmt_ts(t): return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % int(t % 1 * 1000)


# ------------------------------------------------------------------ record encoders
def to_bytes(fmt, r):
    if fmt == "csv":
        s = io.StringIO(); csv.writer(s).writerow([r["seq"], r["ts"], r["delta"], r["dir"], r["text"]])
        return s.getvalue().encode()
    if fmt == "json":
        return json.dumps({k: r[k] for k in ("seq", "ts", "t", "delta", "dir", "text")}, ensure_ascii=False).encode()
    if fmt == "txt":
        return ("#%06d %s +%.3fs %-3s | %s\n" % (r["seq"], r["ts"], r["delta"], r["dir"], r["text"])).encode()
    raw = r["raw"]
    return struct.pack("<dIBH", r["t"], int(r["delta"] * 1e6), DIRS[r["dir"]], len(raw)) + raw


def build(fmt, recs):
    if fmt == "json": return b"[\n" + b",\n".join(to_bytes(fmt, r) for r in recs) + b"\n]\n"
    return {"csv": CSV_HEAD, "bin": MAGIC}.get(fmt, b"") + b"".join(to_bytes(fmt, r) for r in recs)


class LogWriter:
    """One file in one format."""
    def __init__(self, path, fmt):
        self.fmt, self.first = fmt, True
        self.f = open(path, "wb")
        self.f.write({"csv": CSV_HEAD, "bin": MAGIC, "json": b"[\n]\n"}.get(fmt, b"")); self.f.flush()

    def write(self, recs):
        if self.fmt == "json":                       # file stays a valid array after every batch
            self.f.seek(-3, 2)
            parts = []
            for r in recs:
                parts.append((b"\n" if self.first else b",\n") + to_bytes("json", r)); self.first = False
            self.f.write(b"".join(parts) + b"\n]\n")
        else:
            self.f.write(b"".join(to_bytes(self.fmt, r) for r in recs))
        self.f.flush()

    def close(self): self.f.close()


class RotatingWriter:
    """Splits the log into parts every `minutes` minutes (0 = never). Files are created lazily and are
    closed (finalised) at each boundary even when no data arrives, so backup/DLP agents can pick them up."""
    def __init__(self, folder, stem, fmt, minutes):
        self.folder, self.stem, self.fmt = folder, stem, fmt
        self.span = minutes * 60
        self.part, self.deadline, self.lw, self.paths = 0, None, None, []

    def _open(self):
        self.part += 1
        path = os.path.join(self.folder, "%s_part%03d%s" % (self.stem, self.part, EXT[self.fmt]))
        self.lw = LogWriter(path, self.fmt); self.paths.append(path)

    def _advance(self, t):
        self.deadline += self.span * (int((t - self.deadline) // self.span) + 1)

    def write(self, recs):
        cur = []
        for r in recs:
            if self.span:
                if self.deadline is None:
                    self.deadline = r["t"] + self.span
                elif r["t"] >= self.deadline:               # boundary crossed -> next part
                    if cur and self.lw: self.lw.write(cur); cur = []
                    if self.lw: self.lw.close(); self.lw = None
                    self._advance(r["t"])
            if self.lw is None: self._open()
            cur.append(r)
        if cur: self.lw.write(cur)

    def tick(self, now):
        """Called while idle: finalise the current file once its interval has elapsed."""
        if self.span and self.lw and self.deadline is not None and now >= self.deadline:
            self.lw.close(); self.lw = None
            self._advance(now)

    @property
    def current(self): return self.paths[-1] if self.paths else ""

    def close(self):
        if self.lw: self.lw.close(); self.lw = None


def decode_bin(path):
    with open(path, "rb") as f:
        if f.read(5) != MAGIC: raise SystemExit("not an SLG1 log")
        n, seq = struct.calcsize("<dIBH"), 0
        while (h := f.read(n)):
            t, d_us, dr, ln = struct.unpack("<dIBH", h); seq += 1
            print("#%06d %s +%.3fs %s | %s" % (seq, fmt_ts(t), d_us / 1e6, ["RX", "TX", "SYS"][dr], clean(f.read(ln))))


class LineAssembler:
    """Byte stream -> text lines. Terminators: LF, CR, CRLF (any mix). Chunk-boundary independent."""
    MAXLEN = 4096

    def __init__(self):
        self.buf, self.t0, self.skip_lf = bytearray(), None, False

    def _emit(self, partial):
        raw = bytes(self.buf); self.buf.clear()
        return clean(raw) + ("  [partial]" if partial else ""), self.t0, raw

    def feed(self, data):
        out = []
        for b in data:
            if b == 13:
                if self.buf: out.append(self._emit(False))
                self.skip_lf = True
            elif b == 10:
                if self.skip_lf: self.skip_lf = False
                elif self.buf: out.append(self._emit(False))
            else:
                self.skip_lf = False
                if not self.buf: self.t0 = (time.time(), time.monotonic())
                self.buf.append(b)
                if len(self.buf) >= self.MAXLEN: out.append(self._emit(True))
        return out

    def flush(self):
        return [self._emit(True)] if self.buf else []


class Sink(threading.Thread):
    """Writer thread: batches records to disk so the reader never waits for the file system."""
    def __init__(self, rw):
        super().__init__(daemon=True)
        self.rw, self.q, self.dropped, self.lost, self.error, self.quit = rw, queue.Queue(200000), 0, 0, "", False

    def put(self, r):
        try: self.q.put_nowait(r)
        except queue.Full: self.dropped += 1

    def run(self):
        while not (self.quit and self.q.empty()):
            batch = []
            try:
                x = self.q.get(timeout=0.25)
                while True:
                    batch.append(x)
                    if len(batch) >= 5000: break
                    x = self.q.get_nowait()
            except queue.Empty:
                pass
            if not batch:
                try: self.rw.tick(time.time())
                except Exception as e: self.error = str(e)
                continue
            try:
                self.rw.write(batch)
            except Exception as e:                  # disk full / locked file: keep serial reading alive, report it
                self.error = str(e); self.lost += len(batch)
        try: self.rw.close()
        except Exception as e: self.error = str(e)

    def finish(self):
        self.quit = True; self.join(10)


# ------------------------------------------------------------------ settings + validation
def load_settings(path):
    try:
        with open(path, "r", encoding="utf-8") as f: d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(path, d):
    if not path: return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f: json.dump(d, f)
    os.replace(tmp, path)


def check_writable(folder):
    os.makedirs(folder, exist_ok=True)
    fd, p = tempfile.mkstemp(prefix=".sm_write_test_", dir=folder); os.close(fd); os.remove(p)


def parse_connect(b):
    if not isinstance(b, dict): raise ValueError("bad request")
    port = b.get("port")
    if not isinstance(port, str) or not PORT_RE.match(port) or ".." in port: raise ValueError("invalid port name")
    if port.upper() == "SIMULATOR": port = SIM
    v = b.get("baud")
    if isinstance(v, str) and v.strip().isdigit(): v = int(v)
    try:
        ok = not isinstance(v, bool) and isinstance(v, (int, float)) and v == int(v) and 50 <= v <= 4_000_000
    except (ValueError, OverflowError):
        ok = False
    if not ok: raise ValueError("baud must be a whole number from 50 to 4,000,000")
    bytesize = b.get("bytesize", 8)
    try: bytesize = int(bytesize)
    except (TypeError, ValueError): bytesize = 0
    if bytesize not in (5, 6, 7, 8): raise ValueError("data bits must be 5, 6, 7 or 8")
    parity = b.get("parity", "N")
    if parity not in ("N", "E", "O", "M", "S"): raise ValueError("parity must be N, E, O, M or S")
    sb = str(b.get("stopbits", 1))
    sb = {"1.0": "1", "2.0": "2"}.get(sb, sb)
    if sb not in ("1", "1.5", "2"): raise ValueError("stop bits must be 1, 1.5 or 2")
    mode, fmt = b.get("mode", "text"), b.get("fmt", "csv")
    if mode not in ("text", "hex"): raise ValueError("mode must be text or hex")
    if fmt != "off" and fmt not in EXT: raise ValueError("log format must be csv, json, txt, bin or off")
    split = b.get("split", 60)
    if isinstance(split, bool) or not isinstance(split, (int, float)) or split not in SPLITS: raise ValueError("bad split interval")
    return {"port": port, "baud": int(v), "bytesize": bytesize, "parity": parity, "stopbits": sb,
            "mode": mode, "fmt": fmt, "split": int(split)}


def friendly(e):
    s = str(e)
    if "PermissionError" in s or "Access is denied" in s or "Permission denied" in s or "Errno 13" in s:
        return "Access denied: the port is busy or in use by another program (close Arduino IDE, PuTTY, etc.)"
    if "FileNotFoundError" in s or "could not open port" in s or "No such file" in s:
        return "Port not found: %s" % s
    return s


class Monitor:
    def __init__(self, logdir, settings_path=None, warn=""):
        self.settings_path, self.warn = settings_path, warn
        st = load_settings(settings_path) if settings_path else {}
        self.logdir = logdir
        if isinstance(st.get("logdir"), str) and os.path.isabs(st["logdir"]):
            try: check_writable(st["logdir"]); self.logdir = st["logdir"]
            except OSError: self.warn = (warn + " Saved log folder is not writable; using default.").strip()
        self.cv = threading.Condition()
        self.recs, self.base, self.seq, self.epoch, self.last = [], 0, 0, 0, None
        self.stop = threading.Event()
        self.thread = self.ser = self.sink = self.rw = None
        self.simq = []
        self.cfg, self.connected, self.err, self.bytes = {}, False, "", 0

    def add(self, d, text, stamp=None, raw=None):
        wall, mono = stamp or (time.time(), time.monotonic())
        with self.cv:
            self.seq += 1
            delta = 0.0 if self.last is None else max(0.0, mono - self.last)
            self.last = mono
            r = {"seq": self.seq, "t": round(wall, 3), "delta": round(delta, 3), "dir": d, "text": text,
                 "ts": fmt_ts(wall), "raw": raw if raw is not None else text.encode()}
            self.recs.append(r)
            if len(self.recs) > 30000: del self.recs[:5000]; self.base += 5000
            self.cv.notify_all()
        if self.sink: self.sink.put(r)

    def since(self, last, n=5000):
        i = max(0, last - self.base)
        return self.recs[i:i + n]

    def connect(self, b):
        p = parse_connect(b)
        self.disconnect()
        ser = None
        if p["port"] != SIM:
            if serial is None: raise RuntimeError("pyserial missing: pip install pyserial")
            par = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
                   "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}[p["parity"]]
            sb = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}[p["stopbits"]]
            try:
                ser = serial.Serial(p["port"], p["baud"], bytesize=p["bytesize"], parity=par, stopbits=sb,
                                    timeout=0.05, write_timeout=1)
            except Exception as e:
                raise RuntimeError(friendly(e))
        rw = sink = None
        try:
            if p["fmt"] != "off":
                base, k = time.strftime("serial_%Y%m%d_%H%M%S"), 1
                stem = base
                while os.path.exists(os.path.join(self.logdir, stem)):      # never overwrite an earlier session
                    k += 1; stem = "%s_%d" % (base, k)
                folder = os.path.join(self.logdir, stem)
                os.makedirs(folder, exist_ok=True)
                rw = RotatingWriter(folder, stem, p["fmt"], p["split"])
                sink = Sink(rw); sink.start()
        except OSError as e:
            if ser: ser.close()
            raise RuntimeError("Cannot create the log folder (%s): %s" % (self.logdir, e))
        self.ser, self.rw, self.sink = ser, rw, sink
        with self.cv:
            self.recs, self.base, self.seq, self.last = [], 0, 0, None; self.epoch += 1
        frame = "%s%s%s" % (p["bytesize"], p["parity"], p["stopbits"])
        self.cfg = {"port": p["port"], "baud": p["baud"], "mode": p["mode"], "fmt": p["fmt"], "frame": frame, "split": p["split"]}
        self.bytes, self.err, self.connected = 0, "", True
        self.stop.clear()
        self.add("SYS", "Connected %s @ %d baud %s (%s mode, log=%s, split=%s)" %
                 (p["port"], p["baud"], frame, p["mode"], p["fmt"], "off" if not p["split"] else "%d min" % p["split"]))
        self.thread = threading.Thread(target=self.run, daemon=True); self.thread.start()

    def disconnect(self):
        if not self.connected and not self.sink: return
        self.stop.set()
        if self.thread: self.thread.join(2)
        if self.ser:
            try: self.ser.close()
            except Exception: pass  # nosec B110 - best-effort cleanup
            self.ser = None
        if self.connected: self.add("SYS", "Disconnected")
        self.connected = False
        if self.sink: self.sink.finish(); self.sink = None

    def send(self, text, eol):
        if not isinstance(text, str) or len(text) > 4096: raise ValueError("data must be text up to 4096 characters")
        if eol not in ("", "\n", "\r", "\r\n"): raise ValueError("bad line ending")
        if not self.connected: raise RuntimeError("not connected")
        data = (text + eol).encode()
        if self.ser: self.ser.write(data)
        else: self.simq.append(("ECHO:" + text + "\n").encode())
        self.add("TX", text, raw=data)

    def run(self):
        asm, last_rx, n, nxt = LineAssembler(), 0.0, 0, time.monotonic()
        hexmode = self.cfg["mode"] == "hex"
        try:
            while not self.stop.is_set():
                if self.ser:
                    data = self.ser.read(max(1, min(self.ser.in_waiting, 4096)))
                else:
                    time.sleep(0.04); data = b""
                    while self.simq: data += self.simq.pop(0)
                    if time.monotonic() >= nxt:
                        n += 1
                        rnd = random.random  # nosec B311 - simulator test data, not security related
                        data += ("SENSOR,%d,T=%.2f,H=%.1f,P=%.1f\n" % (n, 24 + 2.5 * math.sin(n / 9) + rnd() * .5,
                                 52 + 6 * math.cos(n / 12) + rnd(), 1004 + rnd() * 2)).encode()
                        nxt = time.monotonic() + 0.08
                if data:
                    self.bytes += len(data); last_rx = time.monotonic()
                    if hexmode:
                        self.add("RX", " ".join("%02X" % b for b in data), raw=data)
                    else:
                        for text, stamp, raw in asm.feed(data): self.add("RX", text, stamp, raw)
                elif asm.buf and time.monotonic() - last_rx > 0.15:
                    for text, stamp, raw in asm.flush(): self.add("RX", text, stamp, raw)
        except Exception as e:
            self.err = friendly(e); self.connected = False
            self.add("SYS", "Error: %s" % self.err)


# ------------------------------------------------------------------ port list (cached; background scan)
M, TOKEN = None, None
PORTS = [{"dev": SIM, "vid": "", "pid": "", "desc": "built-in fake sensor", "mfr": "", "sn": ""}]
BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))   # works inside PyInstaller bundles
STATIC = os.path.join(BASE, "static")


def scan():
    global PORTS
    out = [PORTS[0]]
    if serial:
        try:
            for p in sorted(list_ports.comports(), key=lambda p: (p.vid is None, p.device)):
                out.append({"dev": p.device, "vid": "%04X" % p.vid if p.vid is not None else "",
                            "pid": "%04X" % p.pid if p.pid is not None else "",
                            "desc": p.description or "", "mfr": p.manufacturer or "", "sn": p.serial_number or ""})
        except Exception:
            return
    PORTS = out


def scan_loop():
    while True:
        scan(); time.sleep(3)


def scan_now():
    t = threading.Thread(target=scan, daemon=True); t.start(); t.join(2.5)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if sys.platform == "win32": os.startfile(path)  # noqa  # nosec B606 - opens the user's own log folder in Explorer
    elif sys.platform == "darwin": subprocess.Popen(["open", path])  # nosec B603 B607 - fixed argv, folder chosen by the user
    else: subprocess.Popen(["xdg-open", path])  # nosec B603 B607


class Handler(BaseHTTPRequestHandler):
    server_version, sys_version = "SerialMonitor", ""
    timeout = 30                                              # drop stalled / slow-loris clients

    def log_message(self, *a): pass

    def reply(self, body, ctype="application/json", code=200, extra=None):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in SEC_HEADERS.items(): self.send_header(k, v)
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    # ---- access control
    def _port(self): return self.server.server_address[1]
    def host_ok(self): return self.headers.get("Host", "") in ("127.0.0.1:%d" % self._port(), "localhost:%d" % self._port())

    def token_ok(self):
        tok = self.headers.get("X-SM-Token")
        if not tok:
            for part in self.headers.get("Cookie", "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "sm_tok_%d" % self._port(): tok = v
        return bool(tok) and hmac.compare_digest(tok.encode(), TOKEN.encode())

    def guard(self, post=False):
        if not self.host_ok():
            self.reply({"error": "forbidden host"}, code=403); return False
        org = self.headers.get("Origin")
        if org and org not in ("http://127.0.0.1:%d" % self._port(), "http://localhost:%d" % self._port()):
            self.reply({"error": "forbidden origin"}, code=403); return False
        if not self.token_ok():
            self.reply({"error": "unauthorized"}, code=401); return False
        return True

    # ---- routes
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                if not self.host_ok(): return self.reply({"error": "forbidden host"}, code=403)
                k = q.get("k", "")
                if k and hmac.compare_digest(k.encode(), TOKEN.encode()):          # first visit: set cookie, drop token from URL
                    return self.reply(b"", "text/plain", 302, {
                        "Location": "/", "Set-Cookie": "sm_tok_%d=%s; Path=/; HttpOnly; SameSite=Strict" % (self._port(), TOKEN)})
                if not self.token_ok():
                    return self.reply(b"Open Serial Monitor from its own window (access token missing).", "text/plain", 403)
                with open(os.path.join(STATIC, "index.html"), "rb") as f: return self.reply(f.read(), "text/html; charset=utf-8")
            if not self.guard(): return
            if u.path == "/api/ports":
                if q.get("refresh"): scan_now()
                self.reply(PORTS)
            elif u.path == "/api/status":
                s, rw = M.sink, M.rw
                self.reply({"connected": M.connected, "cfg": M.cfg, "err": M.err, "bytes": M.bytes,
                            "logpath": rw.current if rw else "", "part": rw.part if rw else 0,
                            "dropped": s.dropped if s else 0, "lost": s.lost if s else 0, "sinkerr": s.error if s else "",
                            "logdir": M.logdir, "warn": M.warn})
            elif u.path == "/api/log":
                fmt = q.get("fmt", M.cfg.get("fmt", "csv"))
                if fmt not in EXT or not M.seq: return self.reply({"error": "no data"}, code=404)
                with M.cv: recs = list(M.recs)
                name = "serial_export_%s%s" % (time.strftime("%Y%m%d_%H%M%S"), EXT[fmt])
                self.reply(build(fmt, recs), MIME[fmt], extra={"Content-Disposition": 'attachment; filename="%s"' % name})
            elif u.path == "/api/stream":
                self.stream()
            else:
                self.reply({"error": "not found"}, code=404)
        except OSError:
            pass
        except Exception as e:
            self.reply({"error": str(e)}, code=500)

    def stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        for k, v in SEC_HEADERS.items(): self.send_header(k, v)
        self.end_headers()
        last, epoch = 0, M.epoch
        try:
            while True:
                with M.cv:
                    M.cv.wait_for(lambda: M.seq > last or M.epoch != epoch, 10)
                    chunk = M.since(last) if M.epoch == epoch else []
                if M.epoch != epoch: return           # new session: client reopens
                if chunk:
                    self.wfile.write(("data: %s\n\n" % json.dumps({"r": [{k: v for k, v in r.items() if k != "raw"} for r in chunk]})).encode())
                    last = chunk[-1]["seq"]
                    self.wfile.flush(); time.sleep(0.05)   # coalesce: max ~20 messages/s
                else:
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
        except OSError:
            return

    def do_POST(self):
        if not self.guard(post=True): return
        try:
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self.reply({"error": "Content-Type must be application/json"}, code=415)
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY: return self.reply({"error": "request too large"}, code=413)
            try: b = json.loads(self.rfile.read(n) or b"{}")
            except ValueError: return self.reply({"error": "malformed JSON"}, code=400)
            if not isinstance(b, dict): return self.reply({"error": "JSON object expected"}, code=400)
            if self.path == "/api/connect": M.connect(b)
            elif self.path == "/api/disconnect": M.disconnect()
            elif self.path == "/api/send": M.send(b.get("data", ""), b.get("eol", "\n"))
            elif self.path == "/api/open-folder": open_folder(os.path.dirname(M.rw.current) if M.rw and M.rw.current else M.logdir)
            elif self.path == "/api/logdir": self.set_logdir(b.get("path"))
            else: return self.reply({"error": "not found"}, code=404)
            self.reply({"ok": True})
        except ValueError as e:
            self.reply({"error": str(e)}, code=400)
        except RuntimeError as e:
            self.reply({"error": str(e)}, code=409)
        except OSError as e:
            self.reply({"error": friendly(e)}, code=400)
        except Exception as e:                                    # never leak a traceback
            self.reply({"error": "internal error: %s" % type(e).__name__}, code=500)

    def set_logdir(self, p):
        if M.connected: raise RuntimeError("Disconnect before changing the log folder")
        if not isinstance(p, str) or not os.path.isabs(p) or len(p) > 240 or "\x00" in p:
            raise ValueError("Enter a full folder path, e.g. C:\\Logs")
        try: check_writable(p)
        except OSError as e: raise RuntimeError("That folder is not writable: %s" % e)
        M.logdir = os.path.abspath(p)
        st = load_settings(M.settings_path); st["logdir"] = M.logdir; save_settings(M.settings_path, st)


def serve(host, port, logdir, settings_path=None, token=None, warn=""):
    """Create the monitor + HTTP server (not started). Used by the desktop launcher and the tests."""
    global M, TOKEN
    TOKEN = token or secrets.token_urlsafe(24)
    M = Monitor(logdir, settings_path, warn)
    threading.Thread(target=scan_loop, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler); srv.daemon_threads = True
    srv.token = TOKEN
    return srv, M


def main():
    ap = argparse.ArgumentParser(description="Serial Monitor (browser mode)")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--logdir", default="logs"); ap.add_argument("--allow-remote", action="store_true",
                    help="allow binding to a non-loopback address (NOT recommended: exposes your serial ports)")
    ap.add_argument("--no-browser", action="store_true"); ap.add_argument("--decode", metavar="FILE.bin")
    a = ap.parse_args()
    if a.decode: return decode_bin(a.decode)
    if a.host not in ("127.0.0.1", "localhost") and not a.allow_remote:
        sys.exit("Refusing to bind to %s: only 127.0.0.1 is allowed (use --allow-remote to override)." % a.host)
    srv, mon = serve(a.host, a.port, a.logdir, os.path.join(a.logdir, "settings.json"))
    url = "http://127.0.0.1:%d/?k=%s" % (srv.server_address[1], srv.token)
    print("Serial Monitor running. Open:", url, "(Ctrl-C to quit)")
    if serial is None: print("Note: pyserial not installed - only SIMULATOR available")
    if not a.no_browser: threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally: mon.disconnect()


if __name__ == "__main__":
    main()
