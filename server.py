"""
Arattai Dissemination - local server
Chalane ke liye:  python server.py   ->  browser me http://localhost:8765 kholein.
Ye aapke PC par chalta hai aur asli browser (Edge) se web.arattai.in chalata hai.
"""
import base64, json, os, queue, random, re, threading, time, traceback, uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BACKEND = os.environ.get("BACKEND", "api").strip().lower()   # "api" (pyarattai, default) | "browser" (Playwright)
if BACKEND == "browser":
    import arattai_pilot as ap
    from playwright.sync_api import sync_playwright
else:
    import arattai_api as ap
    import arattai_api as ab

def _load_config():
    """config.txt (server.py ke saath) se KEY=VALUE padhta hai. Env vars ko override nahi karta."""
    f = Path(__file__).resolve().parent / "config.txt"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


_load_config()
PORT = int(os.environ.get("PORT", 8765))
TOKEN = os.environ.get("ACCESS_TOKEN", "").strip().strip('"').strip("'")   # cloud me zaroor set karein
AUTOSTART = os.environ.get("AUTOSTART") == "1"
if os.environ.get("CLOUD") == "1" and not TOKEN:
    raise SystemExit("CLOUD mode me ACCESS_TOKEN zaroori hai (warna koi bhi aapke Arattai se bhej sakta hai).")
SHOT = {"png": b""}
ERR = {"png": b"", "ts": 0}
BASE = Path(__file__).resolve().parent
UP = ap.DATA / "uploads"; UP.mkdir(exist_ok=True)

cmds = queue.Queue()
S = {"state": "idle", "msg": "Browser band hai", "job": None, "log": [], "stop": False, "need": ""}
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
    if logged_out(page):
        log("err", "Arattai logout ho gaya hai. Neeche QR scan karke dobara login karein, phir bhejein.")
        set_state("login", "Logout ho gaya - QR scan karein")
        login_loop(page); set_state("ready", "Taiyaar"); return
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
                raise RuntimeError("sahi chat khula nahi - kuch nahi bheja | " + ap.diag_open(page, title))
            for f in files:
                ap.send_file(page, title, f)
                page.wait_for_timeout(1500)
            if text.strip():
                ap.send_text(page, title, text)
            job["ok"] += 1
            log("ok", f"{title}: bheja")
        except Exception as e:
            if is_closed_err(e):
                raise                     # browser band ho gaya: poora job rokna
            status, note = "FAIL", repr(e)[:200]
            job["fail"] += 1
            ap.shot(page, title, "error")
            try:
                ERR["png"] = page.screenshot(); ERR["ts"] = int(time.time())
            except Exception: pass
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
      // rows: 28px se zyada gap = nayi row. Har row ki pehli (sabse upar wali) line = chat ka naam
      const titles = []; let rowStart = -1000;
      const junk = /^(image|video|audio|photo|document|you:?|[a-z]{1,2}:|conversation|chats? & contacts|public channels|frequent contacts|explore channels|hide|follow|typing.*|sticker|gif|location|contact|voice message|missed call|\\d+ (new )?messages?)$/i;
      for (const [y, t] of out) {
        if (y - rowStart > 28) { rowStart = y; if (!junk.test(t)) titles.push(t); }
      }
      return titles;
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


COOKIE_FILE = ap.DATA / "cookies.json"


def logged_out(page):
    """Arattai ka login/QR page dikh raha hai to True."""
    try:
        return page.get_by_text(re.compile(r"Steps to sign in|Sign In Using Mobile Number", re.I)).first.is_visible()
    except Exception:
        return False


def save_cookies(ctx):
    """Session cookies ko file me rakhta hai, taaki restart ke baad login na ud jaye."""
    try:
        ck = ctx.cookies()
        for c in ck:
            if c.get("expires", -1) in (-1, None):
                c["expires"] = time.time() + 30 * 86400
        COOKIE_FILE.write_text(json.dumps(ck), encoding="utf-8")
    except Exception as e:
        print("cookie save fail:", repr(e)[:100])


def load_cookies(ctx):
    try:
        if COOKIE_FILE.exists():
            ctx.add_cookies(json.loads(COOKIE_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        print("cookie load fail:", repr(e)[:100])


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


def is_closed_err(e):
    m = repr(e)
    return "has been closed" in m or "TargetClosed" in m or "Browser closed" in m


def do_click(page, fx, fy):
    vp = page.viewport_size or {"width": 1366, "height": 850}
    page.mouse.click(float(fx) * vp["width"], float(fy) * vp["height"])
    page.wait_for_timeout(1200)
    snap(page)


def worker():
    pw = ctx = page = None
    while True:
        try:
            c = cmds.get(timeout=20)
        except queue.Empty:
            # khaali samay me check: logout to nahi ho gaya?
            if ctx and page and S["state"] == "ready":
                try:
                    if logged_out(page):
                        log("err", "Arattai logout ho gaya. QR scan karein.")
                        set_state("login", "Logout ho gaya - QR scan karein")
                        login_loop(page); save_cookies(ctx)
                        set_state("ready", "Taiyaar"); log("ok", "Arattai login ho gaya")
                    else:
                        save_cookies(ctx)
                except Exception as e:
                    log("err", "watchdog: " + repr(e)[:150])
                    set_state("ready", "Taiyaar")
            continue
        try:
            if c["cmd"] == "start" and ctx is not None:
                try: dead = page.is_closed()
                except Exception: dead = True
                if dead:
                    try: ctx.close()
                    except Exception: pass
                    try: pw.stop()
                    except Exception: pass
                    ctx = pw = page = None
            if c["cmd"] == "start" and ctx is None:
                if pw:
                    try: pw.stop()
                    except Exception: pass
                    pw = None
                set_state("starting", "Browser khul raha hai...")
                pw = sync_playwright().start()
                ctx = ap.launch(pw)
                load_cookies(ctx)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.set_default_timeout(15000)
                set_state("login", "Browser me QR scan karein (agar maanga ho)")
                login_loop(page)
                save_cookies(ctx)
                set_state("ready", "Taiyaar"); log("ok", "Arattai login ho gaya")
            elif c["cmd"] == "send" and page:
                run_job(page, c["job"])
            elif c["cmd"] == "click" and page:
                do_click(page, c["fx"], c["fy"]); c["ev"].set()
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
            if is_closed_err(e) and c["cmd"] != "start":
                try:
                    if ctx: ctx.close()
                except Exception: pass
                try:
                    if pw: pw.stop()
                except Exception: pass
                ctx = pw = page = None
                log("err", "Browser band ho gaya. 'Browser kholo / Login' dabakar dobara shuru karein.")
                set_state("idle", "Browser band ho gaya - dobara Browser kholo / Login dabayein")
                if "ev" in c: c["out"].append({"names": [], "debug": "browser band"}); c["ev"].set()
                continue
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


def api_worker():
    """BACKEND=api: Playwright ke bina, pyarattai se. Commands: start/login/otp/send/chats/close."""
    be = ab.Backend(log)
    def need_login(msg):
        S["need"] = "phone"; set_state("login", msg)
    def wait(sec):
        end = time.time() + sec
        while time.time() < end and not S["stop"]:
            time.sleep(0.25)
    while True:
        try:
            c = cmds.get(timeout=20)
        except queue.Empty:
            if S["state"] == "ready" and not be.keepalive():
                need_login("Session khatam - dobara login karein")
            continue
        try:
            cmd = c["cmd"]
            if cmd == "start":
                if be.client:
                    set_state("ready", "Taiyaar"); continue
                set_state("starting", "Saved login check ho raha hai...")
                if be.try_resume():
                    S["need"] = ""; set_state("ready", "Taiyaar"); log("ok", "Arattai login (saved session)")
                else:
                    need_login("Arattai number daalein")
            elif cmd == "login":
                try:
                    shown = be.begin_login(c["phone"], c["channel"])
                    S["need"] = "otp"; set_state("login", "OTP bheja gaya: %s (Arattai app ya SMS dekhein)" % shown)
                except Exception as e:
                    log("err", "OTP nahi gaya: " + repr(e)[:160]); need_login("Number dobara daalein")
            elif cmd == "otp":
                ok, msg = be.finish_login(c["code"])
                if ok:
                    S["need"] = ""; set_state("ready", "Taiyaar"); log("ok", "Arattai login ho gaya")
                else:
                    log("err", msg); S["need"] = "otp"; set_state("login", msg)
            elif cmd == "send" and be.client:
                job = c["job"]
                set_state("sending", "Bhej rahe hain...")
                with LOCK:
                    S["stop"] = False
                try:
                    ab.run_job(be, job, log, lambda: S["stop"], wait, random.uniform)
                finally:
                    for f in job["files"]:
                        try: Path(f).unlink()
                        except Exception: pass
                    set_state("ready", "Taiyaar")
            elif cmd == "chats" and be.client:
                items = be.list_chats()
                c["out"].append({"names": [i["title"] for i in items], "items": items, "debug": ""}); c["ev"].set()
            elif cmd in ("shot", "click"):
                c["ev"].set()                      # api mode me screenshot nahi hota
            elif cmd == "logout":
                try:
                    (be.client or ab.ArattaiClient.from_session()).logout(True)
                except Exception as e:
                    log("warn", "server-side logout: " + repr(e)[:120])
                be.disconnect(); S["need"] = ""; set_state("idle", "Logout ho gaya")
            elif cmd == "close":
                be.disconnect(); S["need"] = ""; set_state("idle", "Disconnect ho gaya (login saved hai)")
        except Exception as e:
            traceback.print_exc()
            log("err", repr(e)[:200])
            if S["state"] == "sending":
                set_state("ready", "Taiyaar")
            if "ev" in c:
                c["out"].append({"names": [], "debug": "error: " + repr(e)[:200]}); c["ev"].set()


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
        if path == "/api/errshot":
            b = ERR["png"] or b""
            self.send_response(200 if b else 404); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(b))); self.send_header("Cache-Control", "no-store")
            self.cors(); self.end_headers(); self.wfile.write(b); return
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
                self.send_json({"state": S["state"], "msg": S["msg"], "job": S["job"], "log": S["log"][-200:], "errts": ERR["ts"],
                                "backend": BACKEND, "need": S["need"]})
        elif self.path == "/api/chats":
            if S["state"] != "ready":
                return self.send_json({"error": "Pehle browser start/login karein"}, 400)
            c = {"cmd": "chats", "out": [], "ev": threading.Event()}
            cmds.put(c); c["ev"].wait(30)
            r = c["out"][0] if c["out"] and isinstance(c["out"][0], dict) else {"names": [], "debug": "(koi jawab nahi)"}
            self.send_json({"chats": r["names"], "items": r.get("items"), "debug": r["debug"]})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        if TOKEN and self.headers.get("X-Token", "").strip() != TOKEN:
            return self.deny()
        n = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/start":
            cmds.put({"cmd": "start"}); self.send_json({"ok": True})
        elif self.path == "/api/login" and BACKEND == "api":
            try:
                phone = ab.normalize_phone(d.get("phone", ""))
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            ch = d.get("channel") if d.get("channel") in ("app", "sms") else "app"
            cmds.put({"cmd": "login", "phone": phone, "channel": ch}); self.send_json({"ok": True})
        elif self.path == "/api/otp" and BACKEND == "api":
            code = re.sub(r"\D", "", d.get("code", ""))
            if not code: return self.send_json({"error": "OTP likhein"}, 400)
            cmds.put({"cmd": "otp", "code": code}); self.send_json({"ok": True})
        elif self.path == "/api/logout" and BACKEND == "api":
            cmds.put({"cmd": "logout"}); self.send_json({"ok": True})
        elif self.path == "/api/close":
            cmds.put({"cmd": "close"}); self.send_json({"ok": True})
        elif self.path == "/api/click":
            if S["state"] != "ready":
                return self.send_json({"error": "browser taiyaar nahi"}, 400)
            c = {"cmd": "click", "fx": d.get("fx", 0), "fy": d.get("fy", 0), "ev": threading.Event()}
            cmds.put(c); c["ev"].wait(20)
            self.send_json({"ok": True})
        elif self.path == "/api/stop":
            S["stop"] = True; self.send_json({"ok": True})
        elif self.path == "/api/send":
            if S["state"] != "ready":
                return self.send_json({"error": "Abhi taiyaar nahi (state: %s)" % S["state"]}, 400)
            chats = [c if isinstance(c, dict) else c.strip() for c in d.get("chats", [])
                     if (isinstance(c, dict) and c.get("title")) or (isinstance(c, str) and c.strip())]
            if not chats: return self.send_json({"error": "Koi chat nahi chuna"}, 400)
            files = []
            for f in d.get("files", []):
                p = UP / (uuid.uuid4().hex[:8] + "_" + Path(f["name"]).name)
                p.write_bytes(base64.b64decode(f["data"])); files.append(str(p))
            if not files and not d.get("text", "").strip():
                return self.send_json({"error": "Message ya file chahiye"}, 400)
            job = {"chats": chats, "text": d.get("text", ""), "files": files,
                   "min": float(d.get("min", 8)), "max": float(d.get("max", 15)), "live": bool(d.get("live")),
                   "caption": bool(d.get("caption"))}
            S["job"] = job
            cmds.put({"cmd": "send", "job": job})
            self.send_json({"ok": True})
        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    threading.Thread(target=api_worker if BACKEND == "api" else worker, daemon=True).start()
    if AUTOSTART: cmds.put({"cmd": "start"})   # redeploy ke baad saved login se khud khul jaye
    host = os.environ.get("BIND") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    srv = ThreadingHTTPServer((host, PORT), H)
    ng_tok, ng_dom = os.environ.get("NGROK_AUTHTOKEN", ""), os.environ.get("NGROK_DOMAIN", "")
    if ng_tok and ng_dom:
        if not TOKEN:
            print("!! NGROK ke liye config.txt me ACCESS_TOKEN (password) zaroori hai. Tunnel nahi khola gaya.")
        else:
            try:
                from pyngrok import ngrok, conf
                ngrok.set_auth_token(ng_tok)
                t = ngrok.connect(PORT, "http", domain=ng_dom.replace("https://", "").strip("/"))
                print(f"\n==== PERMANENT LINK: https://{ng_dom.replace('https://', '').strip('/')} ====\n")
            except Exception as ex:
                print("!! Tunnel nahi khula:", repr(ex)[:300], "- local link chalta rahega.")
    print(f"Dashboard: http://localhost:{PORT}  (band karne ke liye Ctrl+C)")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
