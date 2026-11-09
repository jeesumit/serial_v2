import http.client, json, os, sys, threading
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]
import server  # noqa: E402

TOKEN = "T" * 32


class Client:
    """Raw loopback client (http.client ignores proxy env vars, like the app's own self-test)."""
    def __init__(self, port, token=TOKEN):
        self.port, self.token = port, token

    def req(self, method, path, body=None, token=True, headers=None, raw=None, ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token: h["X-SM-Token"] = self.token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None: h["Content-Type"] = ctype
        h.update(headers or {})
        c.request(method, path, data, h)
        r = c.getresponse(); b = r.read(); hd = {k.lower(): v for k, v in r.getheaders()}; c.close()
        return r.status, hd, b

    def j(self, method, path, body=None, **kw):
        st, hd, b = self.req(method, path, body, **kw)
        return st, (json.loads(b) if b and hd.get("content-type", "").startswith("application/json") else b)

    def get(self, p, **kw): return self.j("GET", p, **kw)
    def post(self, p, body=None, **kw): return self.j("POST", p, {} if body is None else body, **kw)

    def connect(self, **kw):
        body = {"port": "SIMULATOR", "baud": 115200, "mode": "text", "fmt": "csv", "split": 60}; body.update(kw)
        return self.post("/api/connect", body)


@pytest.fixture
def app(tmp_path):
    srv, mon = server.serve("127.0.0.1", 0, str(tmp_path / "logs"), str(tmp_path / "settings.json"), token=TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    c = Client(srv.server_address[1]); c.srv, c.mon, c.tmp = srv, mon, tmp_path
    yield c
    mon.disconnect(); srv.shutdown(); srv.server_close()
