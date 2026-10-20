"""
Multi-user gateway: ek permanent link, har visitor ka APNA Arattai login.

Har browser ek random ID (X-User) banata hai. Gateway us ID ke liye alag worker process
(server.py, BACKEND=api) chalata hai - alag folder, alag session, alag Signal keys.
Koi bhi user dusre ka login/chats/files nahi dekh sakta. Idle worker band ho jaata hai
(login disk par saved rehta hai, dobara Connect karne par bina OTP ke wapas aata hai).

Env: ACCESS_TOKEN (invite code, zaroori), DATA_DIR, MAX_USERS=10, IDLE_MIN=30, MAX_BODY_MB=320,
     ALLOW_OPEN=1 (bina invite code ke - khatarnak), NODE_SHARED=/opt/pya/node_modules
"""
import hashlib, http.client, json, os, re, secrets, shutil, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", 8765))
TOKEN = os.environ.get("ACCESS_TOKEN", "").strip().strip('"').strip("'")
ROOT = Path(os.environ.get("DATA_DIR", HERE / "data")) / "users"
MAX_USERS = int(os.environ.get("MAX_USERS", 10))
IDLE_SEC = int(float(os.environ.get("IDLE_MIN", 30)) * 60)
REAP_SEC = float(os.environ.get("REAP_SEC", 60))
MAX_BODY = int(float(os.environ.get("MAX_BODY_MB", 320)) * 1048576)
NODE_SHARED = os.environ.get("NODE_SHARED", "/opt/pya/node_modules")
INTERNAL = secrets.token_hex(16)                 # gateway <-> worker ka gupt token
UID_RE = re.compile(r"^[a-f0-9]{32,64}$")
ACTIONS = {"status", "chats", "start", "close", "login", "otp", "send", "stop", "logout", "qr", "diag"}
IDLE_STATUS = {"state": "idle", "msg": "Disconnect hai - Connect dabayein (login saved hai)", "job": None,
               "log": [], "errts": 0, "backend": "api", "need": ""}
W, LOCK = {}, threading.Lock()
FAILS = {}


def user_hash(uid):
    return hashlib.sha256(uid.encode()).hexdigest()[:24]


def alive(w):
    return w and w["proc"].poll() is None


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def get_worker(h, spawn=True):
    with LOCK:
        w = W.get(h)
        if alive(w):
            return w
        if not spawn:
            return None
        if sum(1 for x in W.values() if alive(x)) >= MAX_USERS:
            return "full"
        d = ROOT / h
        (d / ".pyarattai").mkdir(parents=True, exist_ok=True)
        link = d / ".pyarattai" / "node_modules"
        if not link.exists() and Path(NODE_SHARED).is_dir():
            try: link.symlink_to(NODE_SHARED, target_is_directory=True)
            except OSError: pass
        port = free_port()
        env = dict(os.environ, PORT=str(port), BIND="127.0.0.1", DATA_DIR=str(d), ACCESS_TOKEN=INTERNAL,
                   CLOUD="1", AUTOSTART="1", BACKEND="api")
        for k in ("NGROK_AUTHTOKEN", "NGROK_DOMAIN"):
            env.pop(k, None)
        logf = open(d / "worker.log", "ab")
        proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env, cwd=str(HERE),
                                stdout=logf, stderr=subprocess.STDOUT)
        W[h] = w = {"proc": proc, "port": port, "last": time.time(), "dir": d}
    end = time.time() + 25
    while time.time() < end and alive(w):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close(); return w
        except OSError:
            time.sleep(0.2)
    return w if alive(w) else None


def forward(w, method, path, body=None, ctype=None, timeout=120):
    c = http.client.HTTPConnection("127.0.0.1", w["port"], timeout=timeout)
    hdr = {"X-Token": INTERNAL}
    if ctype: hdr["Content-Type"] = ctype
    c.request(method, path, body=body, headers=hdr)
    r = c.getresponse()
    return r.status, r.getheader("Content-Type", "application/json"), r.read()


def kill(w):
    try:
        w["proc"].terminate(); w["proc"].wait(timeout=8)
    except Exception:
        try: w["proc"].kill()
        except Exception: pass


def reaper():
    while True:
        time.sleep(REAP_SEC)
        for h, w in list(W.items()):
            if not alive(w):
                W.pop(h, None); continue
            if time.time() - w["last"] < IDLE_SEC:
                continue
            try:
                st = json.loads(forward(w, "GET", "/api/status", timeout=10)[2]).get("state")
            except Exception:
                st = None
            if st == "sending":
                w["last"] = time.time(); continue       # job chal raha hai - band mat karo
            kill(w); W.pop(h, None)


def forget(h):
    """Server-side Arattai logout + worker band + user ka saara data delete."""
    w = get_worker(h, spawn=False)
    if w:
        try:
            forward(w, "POST", "/api/logout", b"{}", "application/json", 20)
            for _ in range(30):
                if "Logout" in json.loads(forward(w, "GET", "/api/status", timeout=5)[2]).get("msg", ""): break
                time.sleep(0.5)
        except Exception:
            pass
        kill(w); W.pop(h, None)
    shutil.rmtree(ROOT / h, ignore_errors=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def out(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes): body = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*"); self.end_headers()

    def ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def authed(self):
        got = self.headers.get("X-Token", "").strip()
        ip, now = self.ip(), time.time()
        n, t0 = FAILS.get(ip, (0, now))
        if now - t0 > 600: n, t0 = 0, now
        if n >= 20:
            self.out(429, {"error": "bahut galat invite code - 10 minute baad try karein"}); return False
        if TOKEN and not secrets.compare_digest(got, TOKEN):
            FAILS[ip] = (n + 1, t0)
            self.out(401, {"error": "invite code galat/missing", "got_len": len(got)}); return False
        return True

    def route(self, method):
        path = self.path.split("?")[0]
        if method == "GET" and path in ("/", "/index.html"):
            return self.out(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/healthz":
            return self.out(200, {"ok": True})
        if not path.startswith("/api/"):
            return self.out(404, {"error": "not found"})
        if not self.authed():
            return
        uid = self.headers.get("X-User", "").strip()
        if not UID_RE.match(uid):
            return self.out(400, {"error": "X-User missing (page refresh karein)"})
        h, name = user_hash(uid), path[5:]
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            return self.out(413, {"error": "files bahut badi (limit %d MB)" % (MAX_BODY // 1048576)})
        body = self.rfile.read(n) if n else None
        if name == "forget" and method == "POST":
            forget(h); return self.out(200, {"ok": True})
        if name not in ACTIONS:
            return self.out(404, {"error": "not found"})
        w = get_worker(h, spawn=(name != "status"))
        if w is None and name == "status":
            return self.out(200, IDLE_STATUS)
        if w == "full":
            return self.out(503, {"error": "Server abhi bhara hua hai (%d users). Thodi der baad try karein." % MAX_USERS})
        if w is None:
            return self.out(502, {"error": "worker start nahi hua"})
        if name != "status":
            w["last"] = time.time()
        try:
            code, ct, data = forward(w, method, self.path, body, self.headers.get("Content-Type"))
        except Exception as e:
            return self.out(502, {"error": "worker se jawab nahi: " + repr(e)[:100]})
        self.out(code, data, ct)

    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")


if __name__ == "__main__":
    if not TOKEN and os.environ.get("ALLOW_OPEN") != "1":
        raise SystemExit("ACCESS_TOKEN (invite code) zaroori hai. Bina code ke koi bhi aapke server se spam bhej sakta hai "
                         "(chahein to ALLOW_OPEN=1).")
    ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=reaper, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1", PORT), H)
    print("Gateway ready: port %d, max users %d" % (PORT, MAX_USERS))
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        for w in list(W.values()): kill(w)
