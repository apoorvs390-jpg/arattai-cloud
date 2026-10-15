"""
Arattai PILOT (browser automation) - sirf TEST chats ke liye

Ye Arattai ki web site (web.arattai.in) ko ek asli browser me aapki tarah chalata hai:
chat dhoondhna, text likhna, file jodna, bhejna. Encryption browser khud kar deta hai.

Safety:
  * Sirf un chats me bhejta hai jinka naam "Test" se shuru hota hai (SAFE_PREFIX).
  * Bhejne se pehle verify karta hai ki sahi chat khula hai; verify na ho to kuch nahi bhejta.
  * Har chat ka screenshot arattai_shots folder me, aur result arattai_log.csv me save hota hai.

Pehli baar: browser window me QR scan karna hoga. Uske baad login arattai_profile folder me
yaad rahta hai. Us folder ko kisi ke saath share na karein (wo aapke login jaisa hai).

Install:  pip install playwright
Run:      python arattai_pilot.py
"""

import csv
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print("PLAYWRIGHT IMPORT FAIL:", repr(e))
    print("Pehle chalayein:  pip install playwright")
    raise SystemExit(1)

WEB_URL = "https://web.arattai.in"
SAFE_PREFIX = "Test"  # pilot ke dauran sirf is naam se shuru hone wale chats allowed hain

BASE = Path(__file__).resolve().parent
CLOUD = os.environ.get("CLOUD") == "1"          # Railway/Docker par 1
DATA = Path(os.environ.get("DATA_DIR", BASE))   # cloud me persistent volume (/data)
DATA.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = DATA / "arattai_profile"
SHOTS_DIR = DATA / "arattai_shots"
LOG_FILE = DATA / "arattai_log.csv"
SHOTS_DIR.mkdir(exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")


def now():
    return datetime.now().strftime("%H:%M:%S")


def safe_name(s):
    return re.sub(r"[^\w\-]+", "_", s)[:30] or "chat"


def shot(page, title, stage):
    path = SHOTS_DIR / f"{datetime.now().strftime('%H%M%S')}_{safe_name(title)}_{stage}.png"
    try:
        page.screenshot(path=str(path))
    except Exception:
        pass
    return path


def launch(p):
    if CLOUD:
        return p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=True, viewport={"width": 1366, "height": 850}, device_scale_factor=2,
            user_agent=UA, args=["--no-sandbox", "--disable-dev-shm-usage"])
    kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=False, viewport={"width": 1366, "height": 850})
    try:
        # Windows me Edge pehle se hota hai, alag browser download karne ki zaroorat nahi
        return p.chromium.launch_persistent_context(channel="msedge", **kwargs)
    except Exception as e:
        print("Edge se nahi khula:", repr(e)[:150])
        print("Chromium se koshish kar rahe hain (agar error aaye to pehle: playwright install chromium)")
        return p.chromium.launch_persistent_context(**kwargs)


SEARCH_SEL = ('input[placeholder*="earch" i]:visible, input[type="search"]:visible, '
              'input[aria-label*="earch" i]:visible, [role="searchbox"]:visible, '
              '[contenteditable="true"][aria-label*="earch" i]:visible')


def search_box(page):
    """Chat dhundhne ka search box: kai tareeke se dhoondhta hai (placeholder badal gaya ho to bhi)."""
    loc = page.locator(SEARCH_SEL)
    try:
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    # search icon dabakar box kholne ki koshish
    for sel in ('[aria-label*="earch" i]:visible', '[title*="earch" i]:visible', '[class*="search" i]:visible'):
        try:
            icon = page.locator(sel).first
            if icon.count() > 0:
                icon.click(timeout=1500)
                page.wait_for_timeout(600)
                if page.locator(SEARCH_SEL).count() > 0:
                    return page.locator(SEARCH_SEL).first
        except Exception:
            pass
    # aakhri tareeka: baayein panel ke upar ka pehla dikhne wala input
    edge = edge_x(page)
    for el in page.locator("input:visible").all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["x"] < edge and bb["y"] < 220:
            return el
    return page.locator(SEARCH_SEL).first


def wait_for_login(page):
    page.goto(WEB_URL)
    try:
        search_box(page).wait_for(state="visible", timeout=15000)
        print("Pehle se login hai.")
        return
    except Exception:
        pass
    print("Login nahi mila. Khuli hui browser window me QR scan karein (5 minute tak intezaar)...")
    search_box(page).wait_for(state="visible", timeout=300000)
    print("Login ho gaya.")


def edge_x(page):
    """Is x se bayein = chats ki list, daayein = khula hua chat."""
    try:
        return page.evaluate("window.innerWidth") * 0.25
    except Exception:
        return 340


def header_shows(page, title):
    """Khule hue chat ke upar (header me) title dikh raha hai ya nahi."""
    edge = edge_x(page)
    for el in page.get_by_text(title, exact=True).all():
        try:
            box = el.bounding_box()
        except Exception:
            continue
        if box and box["y"] < 200 and box["x"] > edge:
            return True
    return False


def open_chat(page, title):
    """Chat dhoondhkar kholta hai. Click ke kai tareeke aazmata hai aur header se verify karta hai."""
    sb = search_box(page)
    sb.click()
    sb.fill("")
    sb.fill(title)
    page.wait_for_timeout(2000)
    edge = edge_x(page)

    cands = []
    for el in page.get_by_text(title, exact=True).all():
        try:
            bb = el.bounding_box()
        except Exception:
            continue
        if bb and bb["width"] > 0 and bb["height"] > 0 and bb["x"] < edge:   # baayein panel ke result
            cands.append((el, bb))

    js_row = """e => { const row = e.closest('li, [role=listitem], [role=option], [class*=chat], [class*=item], [class*=row]') || e.parentElement || e;
                     row.click(); }"""
    for el, bb in cands:
        for how in ("normal", "force", "row_js", "mouse"):
            try:
                if how == "normal":
                    el.click(timeout=2500)
                elif how == "force":
                    el.click(timeout=2500, force=True)
                elif how == "row_js":
                    el.evaluate(js_row)
                else:
                    page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
                page.wait_for_timeout(1500)
                if header_shows(page, title):
                    return True
            except Exception:
                continue

    # aakhri tareeka: keyboard se pehla search result
    try:
        sb.click()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
    except Exception:
        pass
    return header_shows(page, title)


def find_message_box(page):
    edge = edge_x(page)
    finders = [
        lambda: page.get_by_placeholder(re.compile("Type your message", re.I)),
        lambda: page.locator('[contenteditable="true"]'),
        lambda: page.get_by_role("textbox"),
    ]
    for f in finders:
        best, best_y = None, -1
        try:
            items = f().all()
        except Exception:
            continue
        for el in items:
            try:
                box = el.bounding_box()
            except Exception:
                continue
            if box and box["x"] > edge and box["y"] > best_y:
                best, best_y = el, box["y"]
        if best is not None:
            return best
    return None


def box_text(el):
    try:
        return el.input_value()
    except Exception:
        pass
    try:
        return el.inner_text()
    except Exception:
        return ""


def send_text(page, title, text):
    lines = text.split("\n")
    probe = next((ln.strip() for ln in lines if ln.strip()), "")[:60]
    before = page.get_by_text(probe, exact=False).count() if probe else 0

    box = find_message_box(page)
    if box is None:
        raise RuntimeError("message likhne ka box nahi mila")
    box.click()
    for idx, line in enumerate(lines):
        if idx > 0:
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.type(line, delay=8)
    page.wait_for_timeout(500)
    page.keyboard.press("Enter")
    page.wait_for_timeout(2500)

    box2 = find_message_box(page)
    left = box_text(box2) if box2 is not None else ""
    empty = left.strip() == "" or "Type your message" in left
    after = page.get_by_text(probe, exact=False).count() if probe else 0
    if not empty:
        raise RuntimeError("Enter ke baad bhi box me text bacha hai (shayad bheja nahi gaya)")
    if probe and after <= before:
        raise RuntimeError("message ka bubble dikhai nahi diya (bheja ya nahi, pakka nahi)")


def find_attach_clicks(page):
    """Paperclip button dhoondhne ke tareeke, ek-ek karke try karne ke liye."""
    sels = [
        '[aria-label*="ttach" i]',
        '[title*="ttach" i]',
        '[aria-label*="clip" i]',
        '[title*="clip" i]',
    ]
    for s in sels:
        loc = page.locator(s)
        try:
            n = loc.count()
        except Exception:
            n = 0
        for i in range(n):
            yield f"selector {s}", (lambda l=loc.nth(i): l.click())
    box = find_message_box(page)
    if box is not None:
        bb = box.bounding_box()
        if bb:
            cy = bb["y"] + bb["height"] / 2
            for dx in (20, 32, 45):
                yield f"box ke {dx}px bayein", (lambda x=bb["x"] - dx, y=cy: page.mouse.click(x, y))


def send_file(page, title, path):
    chooser_ok = False
    for label, do_click in find_attach_clicks(page):
        try:
            with page.expect_file_chooser(timeout=2500) as fc:
                do_click()
            fc.value.set_files(path)
            print(f"   (attach button mila: {label})")
            chooser_ok = True
            break
        except Exception:
            continue
    if not chooser_ok:
        shot(page, title, "attach_not_found")
        raise RuntimeError("attach (paperclip) button nahi mila")

    page.wait_for_timeout(2500)
    shot(page, title, "file_dialog")
    clicked = False
    btns = page.get_by_role("button", name=re.compile(r"^\s*send\s*$", re.I))
    for i in range(btns.count()):
        b = btns.nth(i)
        try:
            if b.is_visible():
                b.click()
                clicked = True
                break
        except Exception:
            pass
    if not clicked:
        page.keyboard.press("Enter")
    page.wait_for_timeout(6000)
    shot(page, title, "file_after")


def write_log(rows):
    new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "chat", "status", "note"])
        w.writerows(rows)


def main():
    print("Arattai PILOT. Sirf 'Test' naam wale chats me bhejta hai.\n")
    raw = input("Kin TEST chats me bhejna hai? Naam comma se likhein (default: Test 1,Test 3): ").strip() or "Test 1,Test 3"
    targets = [t.strip() for t in raw.split(",") if t.strip()]
    bad = [t for t in targets if not t.startswith(SAFE_PREFIX)]
    if bad:
        print(f"Ye naam '{SAFE_PREFIX}' se shuru nahi hote, pilot me allowed nahi: {bad}")
        return

    text = input("Message likhein (default: Pilot test message): ").strip() or "Pilot test message"
    path = input("File bhi bhejni hai? Poora path likhein (ya khaali chhodein): ").strip().strip('"')
    if path and not Path(path).is_file():
        print("Ye file nahi mili:", path)
        return
    try:
        lo = float(input("Kam se kam delay seconds (default 8): ").strip() or 8)
        hi = float(input("Zyada se zyada delay seconds (default 15): ").strip() or 15)
    except Exception:
        lo, hi = 8, 15

    print("\nYe chats chune gaye:", ", ".join(targets))
    if input("Sab TEST chats hain? Aage badhne ke liye YES likhein: ").strip() != "YES":
        print("Rok diya. Kuch nahi bheja gaya.")
        return

    with sync_playwright() as p:
        ctx = launch(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(15000)
        try:
            wait_for_login(page)
        except Exception as e:
            print("LOGIN nahi hua:", repr(e)[:200])
            ctx.close()
            return

        ok = bad_count = 0
        rows = []
        for idx, title in enumerate(targets, start=1):
            status, note = "OK", ""
            print(f"\n[{now()}] ({idx}/{len(targets)}) {title}")
            try:
                if not open_chat(page, title):
                    shot(page, title, "open_fail")
                    raise RuntimeError("sahi chat khula/verify nahi hua - kuch nahi bheja")
                shot(page, title, "opened")
                send_text(page, title, text)
                print("   message: bheja (bubble screen par dikha)")
                if path:
                    send_file(page, title, path)
                    print("   file: step poora hua (screenshot aur app me jaakar check karein)")
                ok += 1
            except Exception as e:
                status, note = "FAIL", repr(e)[:200]
                bad_count += 1
                print("   FAIL:", note)
                shot(page, title, "error")
            rows.append([datetime.now().isoformat(timespec="seconds"), title, status, note])
            write_log([rows[-1]])
            if idx < len(targets):
                wait = random.uniform(lo, hi)
                print(f"   ab {wait:.0f} second ruk rahe hain...")
                page.wait_for_timeout(int(wait * 1000))

        print(f"\n[{now()}] Khatam. Safal: {ok}, Fail: {bad_count}")
        print("Screenshots:", SHOTS_DIR)
        print("Log:", LOG_FILE)
        ctx.close()


if __name__ == "__main__":
    main()
