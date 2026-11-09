"""Create sample logs in samples/ using the real writers:  python tools/make_samples.py"""
import math, os, random, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import server

def rec(seq, t, prev, d, text):
    return {"seq": seq, "t": round(t, 3), "delta": round(t - prev, 3) if prev else 0.0, "dir": d, "text": text,
            "ts": server.fmt_ts(t), "raw": text.encode()}

def make(start, seconds, hz=5):
    random.seed(1); prev = start
    recs = [rec(1, start, None, "SYS", "Connected SIMULATOR @ 115200 baud 8N1 (sample data)")]
    for n in range(1, seconds * hz + 1):
        t = start + n / hz
        recs.append(rec(len(recs) + 1, t, prev, "RX", "SENSOR,%d,T=%.2f,H=%.1f,P=%.1f" % (
            n, 24 + 2.5 * math.sin(n / 9) + random.random() * .5, 52 + 6 * math.cos(n / 12) + random.random(), 1004 + random.random() * 2)))
        prev = t
        if n % 100 == 0:
            recs.append(rec(len(recs) + 1, t + .01, prev, "TX", "PING")); prev = t + .01
    return recs

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "samples")
    os.makedirs(os.path.join(out, "split_demo_1min"), exist_ok=True)
    start = time.mktime((2026, 11, 8, 10, 0, 0, 0, 0, -1))
    r90 = make(start, 90)
    for f, ext in server.EXT.items():
        open(os.path.join(out, "sample_log" + ext), "wb").write(server.build(f, r90))
    rw = server.RotatingWriter(os.path.join(out, "split_demo_1min"), "demo", "csv", 1)   # 179 s -> 3 files
    rw.write(make(start, 179)); rw.close()
    print("samples written:", sorted(os.listdir(out)), sorted(os.listdir(os.path.join(out, "split_demo_1min"))))
