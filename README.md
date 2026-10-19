# Arattai Dissemination Tool (API mode)

Bulk-send **text, photo, video, document** to many Arattai groups / contacts - bina browser ke.
Login, chat list aur text `irotechlab-pyarattai` se; media upload Arattai web ke `files.arattai.in/webupload` se.
Purana Playwright (browser) tareeka bhi bacha hai: `BACKEND=browser`.

## Local (Windows)
1. Python 3.12+ aur Node.js 18+ install karein.
2. `start.bat` double-click -> http://localhost:8765
3. **Connect / Login** -> number -> OTP (Arattai app/SMS). Login `~/.pyarattai/` me saved rehta hai.

## Permanent shareable link (multi-user, Railway)
Ek link, har visitor ka APNA Arattai login (alag worker + alag folder; koi kisi ka data nahi dekh sakta).
1. Repo ko **private** GitHub repo me daalein. Railway > New Project > Deploy from GitHub (`Dockerfile` auto-detect).
2. Variables: `ACCESS_TOKEN` = invite code (lamba, random). Optional: `MAX_USERS` (default 10), `IDLE_MIN` (default 30).
3. Volume add karein, mount path `/data` (sabke login yahin saved rehte hain; bina iske har deploy par sabka OTP dobara).
4. Settings > Networking > Generate Domain -> ye aapka permanent link.
5. Link + invite code logon ko dein. Wo page kholkar code daalenge (Save), Connect -> apna number -> OTP.
Single-user (sirf aapke liye) ke liye `Dockerfile.single`; browser mode ke liye `Dockerfile.browser`.

## Pehli baar zaroor karein (unofficial endpoints hain)
1. LIVE MODE **off** rakhein. Apne "Test ..." naam ke group/DM me ek photo, ek video, ek PDF bhejein.
2. Log me "upload tareeka 'a'/'ws' chala; server jawab: ..." line dekhein, aur Arattai app me check karein ki media sahi dikha.
3. Tabhi LIVE MODE on karein.

## Kya chalta hai / nahi
- Groups (non-E2EE): text + media. DM/contacts: text E2EE (Signal) se; **media DM me server accept kare tabhi** (test karein).
- E2EE groups: text bhi supported nahi (library limit).
- Safety: Test mode, 5 lagataar fail par auto-stop, throttle aaye to 60s pause fir dobara aane par job stop, max file `MAX_FILE_MB` (default 64).
- Bulk bhejna Arattai ke terms/spam rules ke against ja sakta hai - delay 8-15s+ rakhein, sirf unhe bhejein jo expect karte hain.

## Tests
`pip install pytest && pytest tests` (offline, fake server).

## Multi-user ke baare me
- Server par logon ke Arattai login saved hote hain - sirf unhe invite dein jinpar bharosa ho. "Logout + mera data delete" button se koi bhi apna data hata sakta hai.
- Sab ek hi server IP se bhejenge; zyada logon/zyada volume par Arattai us IP ko limit/flag kar sakta hai.
- Har active user ~50 MB RAM leta hai; idle users ka worker 30 min baad band hota hai (login saved rehta hai).
