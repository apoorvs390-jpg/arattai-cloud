"""
Arattai API backend (no browser).

pyarattai (irotechlab-pyarattai) se login / chats / text bhejna, aur `arattai` PyPI package me
mile web upload endpoint (files.arattai.in/webupload) se photo / video / document bhejna.

NOTE: dono libraries unofficial hain (web app ke endpoints reverse-engineer kiye gaye). Upload
endpoint ke headers live Arattai par aapko pehle ek "Test" chat me verify karne honge.
"""
from __future__ import annotations

import csv
import json
import mimetypes
import os
import re
import stat
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_DIR", BASE))
DATA.mkdir(parents=True, exist_ok=True)
if os.environ.get("DATA_DIR"):
    # pyarattai apna session/keys ~/.pyarattai me rakhta hai -> cloud me persistent volume par le jao
    os.environ["HOME"] = str(DATA)
    os.environ["USERPROFILE"] = str(DATA)

try:
    from pyarattai import ArattaiClient, Auth, Session
    from pyarattai.constants import SESSION_FILE
    from pyarattai.errors import ArattaiError, AuthError, RateLimitError
except ImportError:
    raise SystemExit("Pehle chalayein:  pip install irotechlab-pyarattai")

SAFE_PREFIX = "Test"          # LIVE MODE off ho to sirf is naam se shuru hone wale chats allowed
LOG_FILE = DATA / "arattai_log.csv"
MAX_FILE_MB = float(os.environ.get("MAX_FILE_MB", 64))
KEEPALIVE_SEC = 600
RATE_PAUSE_SEC = 60
MAX_CONSEC_FAIL = 5


def write_log(rows):
    new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "chat", "status", "note"])
        w.writerows(rows)


def media_kind(path):
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    top = mime.split("/")[0]
    return mime, (top if top in ("image", "video", "audio") else "document")


def normalize_phone(raw):
    """'9876543210' / '+91 98765 43210' / '91-9876543210' -> '91-9876543210'."""
    d = re.sub(r"[^\d]", "", raw or "")
    if len(d) == 10:
        return "91-" + d
    if len(d) == 12 and d.startswith("91"):
        return "91-" + d[2:]
    if len(d) >= 11:
        return d[:-10] + "-" + d[-10:]
    raise ValueError("Number galat lag raha hai (10 digit, ya 91 ke saath)")


def persist_session(client, path=SESSION_FILE):
    """Cookies + ids ko session.json me likhta hai (pyarattai ka save_session maujood nahi hai)."""
    p = Path(os.path.expanduser(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        blob = json.loads(p.read_text())
    except Exception:
        blob = {}
    blob["cookies"] = [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
                        "secure": c.secure, "expires": c.expires} for c in client.s.http.cookies]
    for k in ("uid", "mobile", "device_id", "registration_id", "session_id"):
        blob[k] = getattr(client, k, None)
    p.write_text(json.dumps(blob, indent=2))
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


class Backend:
    def __init__(self, log=print):
        self.log = log
        self.client = None
        self._pending = None        # (Session, Auth, phone) OTP ke beech
        self._chats = []            # pyarattai Chat objects (cache)
        self._up_mode = None        # upload ka kaunsa x-cliq-sid tareeka chala ("a" / "ws")
        self._last_keepalive = time.time()

    # ------------------------------------------------------------ login
    def try_resume(self):
        try:
            c = ArattaiClient.from_session()
            c.get_chats(limit=5)                 # session zinda hai ya nahi
        except Exception as e:
            self.log("info", "Saved session nahi chali: " + repr(e)[:120])
            return False
        self.client = c
        persist_session(c)
        return True

    def begin_login(self, phone, channel="app"):
        s = Session()
        a = Auth(s)
        a.start()
        a.lookup(phone)
        a.send_otp(channel=channel)
        self._pending = (s, a, phone)
        return a.r_mobile or a.e_mobile or phone

    def finish_login(self, code):
        if not self._pending:
            return False, "Pehle number daal kar OTP mangwayein"
        s, a, phone = self._pending
        resp = a.verify_otp(code.strip())
        rc = str((resp or {}).get("code") or (resp or {}).get("status_code") or "") if isinstance(resp, dict) else ""
        if rc not in ("SI200", "200"):
            return False, "OTP galat/expire ({}). Dobara try karein ya naya OTP mangwayein".format(rc or "?")
        a.mobile = phone
        a.establish_chat_session()
        a.mint_x_tkp_token()
        a.fetch_session_meta()
        if not a.uid:
            a.uid = a.identifier
        a.save(SESSION_FILE)
        client = ArattaiClient(s, {"uid": a.uid, "mobile": a.mobile, "device_id": a.device_id,
                                   "registration_id": a.registration_id, "session_id": a.session_id})
        self._register_signal(client)
        persist_session(client)
        self.client, self._pending = client, None
        return True, "ok"

    def _register_signal(self, client):
        """DM (E2EE) text ke liye Signal keys. Fail ho to bhi login chalega (groups/files par asar nahi)."""
        try:
            from pyarattai.signal import SIGNAL_FILE, register_signal_keys
            if os.path.exists(str(SIGNAL_FILE)):
                return
            if not client.device_id:
                import secrets
                client.device_id = str(secrets.randbelow(8_000_000) + 1_000_000)
            self.log("info", "Signal keys register ho rahe hain (pehli baar, Node.js chahiye)...")
            register_signal_keys(client, int(client.device_id))
            self.log("ok", "Signal keys register ho gaye")
        except Exception as e:
            self.log("warn", "Signal setup nahi hua (DM text E2EE nahi chalega): " + repr(e)[:150])

    def keepalive(self):
        """Idle me session zinda rakhna + cookies save. False = session mar gaya."""
        if not self.client or time.time() - self._last_keepalive < KEEPALIVE_SEC:
            return True
        self._last_keepalive = time.time()
        try:
            self.client.get_chats(limit=1)
            persist_session(self.client)
            return True
        except Exception as e:
            self.log("err", "Session check fail: " + repr(e)[:150])
            return False

    def disconnect(self):
        self.client = self._pending = None

    # ------------------------------------------------------------ chats
    def list_chats(self):
        self._chats = [c for c in self.client.get_chats(limit=2000) if c.id]
        self._chats.sort(key=lambda c: c.last_msg_time or 0, reverse=True)
        out = []
        for c in self._chats:
            title = (c.title or (c.raw or {}).get("name") or "").strip()
            if not title:
                continue
            kind = "group" if c.is_group else ("contact" if c.is_e2ee else "other")
            out.append({"id": c.id, "title": title, "kind": kind, "e2ee": bool(c.is_e2ee)})
        return out

    def resolve(self, rec):
        """rec: {'id','title'} ya sirf naam (str). -> (chat_dict, error)"""
        items = {i["id"]: i for i in self._items()}
        if isinstance(rec, dict) and rec.get("id"):
            it = items.get(rec["id"])
            return (it, "") if it else (None, "chat id list me nahi mila (list dobara laayein)")
        name = (rec["title"] if isinstance(rec, dict) else str(rec)).strip().casefold()
        hits = [i for i in items.values() if i["title"].casefold() == name]
        if len(hits) == 1:
            return hits[0], ""
        if not hits:
            return None, "is naam ka chat nahi mila"
        return None, "{} chats ka naam same hai - 'list se' chun kar add karein".format(len(hits))

    def _items(self):
        return [{"id": c.id, "title": (c.title or "").strip(), "kind": "group" if c.is_group else "contact",
                 "e2ee": bool(c.is_e2ee)} for c in self._chats]

    # ------------------------------------------------------------ sending
    def send_text(self, chat_id, text):
        return self.client.send(chat_id, text)

    def send_media(self, chat_id, path, caption=""):
        """Photo / video / document upload. Server ka jawab (chhota snippet) lautata hai."""
        p = Path(path)
        mime, _ = media_kind(p)
        data = p.read_bytes()                      # bytes: retry me bhi poori file jaati hai
        modes = [self._up_mode] if self._up_mode else ["a", "ws"]
        last = "upload fail"
        for mode in modes:
            ts = int(time.time() * 1000)
            sid = "a"
            if mode == "ws":
                sid = (self.client._fresh_ws_sid() or "") if hasattr(self.client, "_fresh_ws_sid") else ""
            headers = {
                "x-cliq-content-type": mime, "Content-Type": mime,
                "x-cliq-comment": quote(caption) if caption else "",
                "upload-id": "{}_{}".format(chat_id, ts), "x-cliq-msgid": str(ts),
                "file-name": quote(p.name), "x-service": "arattai", "x-cliq-sid": sid,
                "x-client-time-utc": str(ts), "x-cliq-id": chat_id,
            }
            try:
                headers.update(self.client.s._zcsrf_header())
            except Exception:
                pass
            resp = self.client.s.request("POST", "/webupload", base="files", raw=True,
                                         data=data, headers=headers, timeout=300)
            body = (resp.text or "")[:300]
            if resp.status_code == 429 or "request_limit_exceeded" in body:
                raise RateLimitError("Arattai throttle: " + body[:100])
            ok = 200 <= resp.status_code < 300
            try:
                j = resp.json()
                if isinstance(j, dict) and (j.get("error") or str(j.get("code", "")).upper().startswith(("ERR", "IN"))):
                    ok = False
            except ValueError:
                pass
            if ok:
                if self._up_mode != mode:
                    self.log("info", "upload tareeka '{}' chala; server jawab: {}".format(mode, body[:120]))
                self._up_mode = mode
                return body
            last = "upload HTTP {} {}".format(resp.status_code, body[:150])
        raise ArattaiError(last)


# ---------------------------------------------------------------- bulk job
def run_job(be, job, log, stopped, wait, rng):
    """Ek broadcast chalata hai. job dict me total/done/ok/fail/current update hote rehte hain."""
    chats, text, files = job["chats"], job["text"], job["files"]
    lo, hi, live = job["min"], job["max"], job["live"]
    job["total"] = len(chats)
    job["done"] = job["ok"] = job["fail"] = 0

    for f in files:                                  # bhejne se pehle size check
        mb = Path(f).stat().st_size / 1048576
        if mb > MAX_FILE_MB:
            log("err", "{} {:.0f} MB hai (limit {:.0f} MB). Kuch nahi bheja.".format(Path(f).name, mb, MAX_FILE_MB))
            job["total"] = 0
            return
    cap_ok = bool(job.get("caption")) and bool(files) and text.strip() and text.isascii()
    if job.get("caption") and files and text.strip() and not text.isascii():
        log("warn", "Caption me non-ASCII (Hindi) text hai - caption mode band, text alag message me jayega")

    be.list_chats()                                  # taaza chat list (naam -> id)
    seen, consec = set(), 0
    for i, rec in enumerate(chats, 1):
        if stopped():
            log("warn", "Stop dabaya gaya, ruk gaye.")
            break
        label = rec["title"] if isinstance(rec, dict) else str(rec)
        job["current"] = label
        status, note, done_parts = "OK", "", 0
        try:
            chat, err = be.resolve(rec)
            if not chat:
                raise RuntimeError(err)
            label = chat["title"]
            if chat["id"] in seen:
                raise RuntimeError("duplicate (ye chat pehle aa chuka)")
            seen.add(chat["id"])
            if not live and not label.startswith(SAFE_PREFIX):
                raise RuntimeError("TEST MODE me sirf 'Test' naam wale chats allowed hain")
            parts = [("file", f, text if (cap_ok and n == 0) else "") for n, f in enumerate(files)]
            if text.strip() and not cap_ok:
                parts.append(("text", text, ""))
            for kind, arg, cap in parts:
                for attempt in (1, 2):
                    try:
                        if kind == "file":
                            be.send_media(chat["id"], arg, cap)
                            if done_parts + 1 < len(parts):
                                wait(1.5)
                        else:
                            be.send_text(chat["id"], arg)
                        break
                    except RateLimitError:
                        if attempt == 2:
                            job["abort"] = True
                            raise
                        log("warn", "Arattai ne throttle kiya, {}s ruk kar dobara try".format(RATE_PAUSE_SEC))
                        wait(RATE_PAUSE_SEC)
                done_parts += 1
            job["ok"] += 1
            consec = 0
            flag = " (E2EE-type chat: server ne file accept ki)" if (files and chat["e2ee"]) else ""
            log("ok", "{}: bheja{}".format(label, flag))
        except Exception as e:
            status, note = "FAIL", repr(e)[:200]
            if done_parts:
                status = "PARTIAL"
            job["fail"] += 1
            consec += 1
            log("err", "{}: {}".format(label, note))
        job["done"] = i
        write_log([[datetime.now().isoformat(timespec="seconds"), label, status, note]])
        if job.get("abort"):
            log("err", "Throttle dobara aaya - account bachane ke liye job roka gaya. Kuch der baad try karein.")
            break
        if consec >= MAX_CONSEC_FAIL:
            log("err", "{} chats lagataar fail - job roka gaya (login/upload check karein).".format(consec))
            break
        if i < len(chats) and not stopped():
            w = rng(lo, hi)
            log("info", "{:.0f}s ruk rahe hain".format(w))
            wait(w)
    job["current"] = ""
    log("info", "Khatam. Safal {}, Fail {}".format(job["ok"], job["fail"]))
