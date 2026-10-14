"""
Arattai Dissemination - local server
Chalane ke liye:  python server.py   ->  browser me http://localhost:8765 kholein.
Ye aapke PC par chalta hai aur asli browser (Edge) se web.arattai.in chalata hai.
"""
import base64, json, os, queue, random, re, threading, time, traceback, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import arattai_pilot as ap
from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("PORT", 8765))
TOKEN = os.environ.get("ACCESS_TOKEN", "").strip().strip('"').strip("'")   # cloud me zaroor set karein
AUTOSTART = os.environ.get("AUTOSTART") == "1"
SHOT = {"png": b""}
BASE = Path(__file__).resolve().parent
UP = ap.DATA / "uploads"; UP.mkdir(exist_ok=True)

cmds = queue.Queue()
S = {"state": "idle", "msg": "Browser band hai", "job": None, "log": [], "stop": False}
LOCK = threading.Lock()


def log(kind, text):
    with LOCK:
        S["log"].append({"t": datetime.now().strftime("%H:%M:%S"), "k": kind, "m": text})
        S["log"] = S["log"][-500:]
    print(kind, text)


def set_state(st, msg=""):
    with LOCK:
        S["state"], S["msg"] = st, msg


def run_job(page, job):
    chats, text, files = job["chats"], job["text"], job["files"]
    lo, hi, live = job["min"], job["max"], job["live"]
    job["total"] = len(chats); job["done"] = job["ok"] = job["fail"] = 0
    with LOCK:
        S["stop"] = False
    set_state("sending", "Bhej rahe hain...")
    for i, title in enumerate(chats, 1):
        if S["stop"]:
            log("warn", "Stop dabaya gaya, ruk gaye."); break
        job["current"] = title
        status, note = "OK", ""
        try:
            if not live and not title.startswith(ap.SAFE_PREFIX):
                raise RuntimeError("TEST MODE me sirf 'Test' naam wale chats allowed hain")
            if not ap.open_chat(page, title):
                ap.shot(page, title, "open_fail")
                raise RuntimeError("sahi chat khula nahi - kuch nahi bheja")
            for f in files:
                ap.send_file(page, title, f)
                page.wait_for_timeout(1500)
            if text.strip():
                ap.send_text(page, title, text)
            job["ok"] += 1
            log("ok", f"{title}: bheja")
        except Exception as e:
            status, note = "FAIL", repr(e)[:200]
            job["fail"] += 1
            ap.shot(page, title, "error")
            log("err", f"{title}: {note}")
            # chat khula rah gaya ho to wapas theek state me aane ki koshish
            try: page.keyboard.press("Escape")
            except Exception: pass
        job["done"] = i
        ap.write_log([[datetime.now().isoformat(timespec="seconds"), title, status, note]])
        if i < len(chats) and not S["stop"]:
            w = random.uniform(lo, hi)
            log("info", f"{w:.0f}s ruk rahe hain")
            end = time.time() + w
            while time.time() < end and not S["stop"]:
                page.wait_for_timeout(500)
    job["current"] = ""
    log("info", f"Khatam. Safal {job['ok']}, Fail {job['fail']}")
    set_state("ready", "Taiyaar")


def scrape_chats(page):
    """Baayein panel ke chat naam (scroll karke). Layout pakka nahi, isliye khaali aaye to debug text bhi lautata hai."""
    edge = ap.edge_x(page)
    # Agar chat panel "Unread" jaise filter par atka ho to "Show all" dabakar poori list kholo
    for label in ("Show all", "All chats", "All"):
        try:
            el = page.get_by_text(label, exact=True).first
            bb = el.bounding_box() if el.is_visible() else None
            if bb and bb["x"] < edge * 1.6 and bb["y"] > 60:
                el.click(timeout=2000); page.wait_for_timeout(1500); break
        except Exception:
            pass
    collect = """(edge) => {
      const out = [];
      const skip = /^(you're all caught up!?|you have no unread messages|show all|unread|all|groups|chats|search chats|\\d{1,2}[:\\/.]\\d{1,2}.*|today|yesterday|now|online|typing\\.*|\\d+)$/i;
      for (const el of document.querySelectorAll('*')) {
        if (el.children.length) continue;
        const r = el.getBoundingClientRect();
        const t = (el.textContent || '').trim();
        if (r.width && r.height && r.x < edge && r.y > 110 && r.y < innerHeight && t.length > 1 && t.length < 70 && !skip.test(t)) out.push([r.y, t]);
      }
      out.sort((a, b) => a[0] - b[0]);
      return out.map(x => x[1]);
    }"""
    scroll = """(edge) => {
      let el = document.elementFromPoint(Math.min(edge * 0.5, 160), innerHeight * 0.6);
      while (el && el !== document.body) {
        if (el.scrollHeight > el.clientHeight + 20) {
          const before = el.scrollTop; el.scrollTop += el.clientHeight * 0.8;
          return el.scrollTop !== before;
        }
        el = el.parentElement;
      }
      return false;
    }"""
    seen, order, idle = set(), [], 0
    for _ in range(40):
        try:
            names = page.evaluate(collect, edge)
        except Exception:
            names = []
        new = [n for n in names if n not in seen]
        for n in new:
            seen.add(n); order.append(n)
        idle = 0 if new else idle + 1
        try:
            moved = page.evaluate(scroll, edge)
        except Exception:
            moved = False
        if not moved or idle >= 3:
            break
        page.wait_for_timeout(700)
    try: page.evaluate("(e)=>{let el=document.elementFromPoint(Math.min(e*0.5,160),innerHeight*0.6);while(el&&el!==document.body){if(el.scrollTop>0){el.scrollTop=0;break}el=el.parentElement}}", edge)
    except Exception: pass
    debug = ""
    if not order:
        try: debug = page.inner_text("body")[:1200]
        except Exception: debug = "(page text nahi mila)"
    return {"names": order, "debug": debug}


def qr_element(page):
    """Page me sabse ban-ta QR jaisa (lagbhag square, 120-600px) canvas/img dhoondhta hai."""
    best, area = None, 0
    for el in page.locator("canvas, img, svg").all():
        try:
            b = el.bounding_box()
        except Exception:
            continue
        if not b or not (120 <= b["width"] <= 600): continue
        if abs(b["width"] - b["height"]) > 20: continue
        if b["width"] * b["height"] > area:
            best, area = el, b["width"] * b["height"]
    return best


def snap(page, qr=False):
    try:
        if qr:
            el = qr_element(page)
            if el is not None:
                SHOT["png"] = el.screenshot(); return
        SHOT["png"] = page.screenshot()
    except Exception: pass


def refresh_qr(page):
    try:
        el = page.get_by_text(re.compile(r"click to reload", re.I)).first
        if el.is_visible():
            el.click(timeout=3000)
            page.wait_for_timeout(1500)
    except Exception:
        pass


def login_loop(page):
    page.goto(ap.WEB_URL)
    end = time.time() + 600
    while time.time() < end:
        try:
            if ap.search_box(page).is_visible():
                return
        except Exception: pass
        refresh_qr(page)         # QR expire hua ("Click to reload") to khud click karo
        snap(page, qr=True)      # dashboard par QR dikhane ke liye (sirf QR crop)
        page.wait_for_timeout(2000)
    raise RuntimeError("10 minute me login nahi hua")


def worker():
    pw = ctx = page = None
    while True:
        c = cmds.get()
        try:
            if c["cmd"] == "start" and ctx is None:
                if pw:
                    try: pw.stop()
                    except Exception: pass
                    pw = None
                set_state("starting", "Browser khul raha hai...")
                pw = sync_playwright().start()
                ctx = ap.launch(pw)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(15000)
                set_state("login", "Browser me QR scan karein (agar maanga ho)")
                login_loop(page)
                set_state("ready", "Taiyaar"); log("ok", "Arattai login ho gaya")
            elif c["cmd"] == "send" and page:
                run_job(page, c["job"])
            elif c["cmd"] == "shot" and page:
                snap(page); c["ev"].set()
            elif c["cmd"] == "chats" and page:
                c["out"].append(scrape_chats(page)); c["ev"].set()
            elif c["cmd"] == "close" and ctx:
                ctx.close(); pw.stop(); ctx = pw = page = None
                set_state("idle", "Browser band hai")
        except Exception as e:
            traceback.print_exc()
            log("err", repr(e)[:200])
            if c["cmd"] == "start":
                try:
                    if ctx: ctx.close()
                except Exception: pass
                try:
                    if pw: pw.stop()      # warna agli baar "Sync API inside asyncio loop" error aata hai
                except Exception: pass
                ctx = pw = page = None
                set_state("idle", "Login nahi hua - dobara try karein")
            elif S["state"] == "sending":
                set_state("ready", "Taiyaar")
            if "ev" in c: c["out"].append({"names": [], "debug": "error: " + repr(e)[:200]}); c["ev"].set()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.cors(); self.end_headers(); self.wfile.write(b)

    def cors(self):
        # GitHub Pages wale dashboard ko is local server se baat karne dene ke liye
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204); self.cors(); self.end_headers()

    def deny(self):
        got = self.headers.get("X-Token", "").strip()
        self.send_json({"error": "token galat/missing", "got_len": len(got), "want_len": len(TOKEN)}, 401)

    def authed(self):
        return not TOKEN or self.headers.get("X-Token", "") == TOKEN or self.path.split("?")[0].endswith(("/", ".html")) 

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in ("/", "/index.html") and TOKEN and self.headers.get("X-Token", "").strip() != TOKEN:
            return self.deny()
        if path == "/api/screen":
            if S["state"] in ("ready", "login"):
                c = {"cmd": "shot", "ev": threading.Event()}
                if S["state"] == "ready": cmds.put(c); c["ev"].wait(15)
            b = SHOT["png"] or b""
            self.send_response(200 if b else 404); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
            self.cors(); self.end_headers(); self.wfile.write(b); return
        if self.path in ("/", "/index.html"):
            b = (BASE / "index.html").read_bytes()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        elif self.path == "/api/status":
            with LOCK:
                self.send_json({"state": S["state"], "msg": S["msg"], "job": S["job"], "log": S["log"][-200:]})
        elif self.path == "/api/chats":
            if S["state"] != "ready":
                return self.send_json({"error": "Pehle browser start/login karein"}, 400)
            c = {"cmd": "chats", "out": [], "ev": threading.Event()}
            cmds.put(c); c["ev"].wait(30)
            r = c["out"][0] if c["out"] and isinstance(c["out"][0], dict) else {"names": [], "debug": "(koi jawab nahi)"}
            self.send_json({"chats": r["names"], "debug": r["debug"]})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if TOKEN and self.headers.get("X-Token", "").strip() != TOKEN:
            return self.deny()
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/start":
            cmds.put({"cmd": "start"}); self.send_json({"ok": True})
        elif self.path == "/api/close":
            cmds.put({"cmd": "close"}); self.send_json({"ok": True})
        elif self.path == "/api/stop":
            S["stop"] = True; self.send_json({"ok": True})
        elif self.path == "/api/send":
            if S["state"] != "ready":
                return self.send_json({"error": "Abhi taiyaar nahi (state: %s)" % S["state"]}, 400)
            chats = [c.strip() for c in d.get("chats", []) if c.strip()]
            if not chats: return self.send_json({"error": "Koi chat nahi chuna"}, 400)
            files = []
            for f in d.get("files", []):
                p = UP / (uuid.uuid4().hex[:8] + "_" + Path(f["name"]).name)
                p.write_bytes(base64.b64decode(f["data"])); files.append(str(p))
            if not files and not d.get("text", "").strip():
                return self.send_json({"error": "Message ya file chahiye"}, 400)
            job = {"chats": chats, "text": d.get("text", ""), "files": files,
                   "min": float(d.get("min", 8)), "max": float(d.get("max", 15)), "live": bool(d.get("live"))}
            S["job"] = job
            cmds.put({"cmd": "send", "job": job})
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    if AUTOSTART: cmds.put({"cmd": "start"})   # redeploy ke baad saved login se khud khul jaye
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    srv = ThreadingHTTPServer((host, PORT), H)
    print(f"Dashboard: http://localhost:{PORT}  (band karne ke liye Ctrl+C)")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
