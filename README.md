# Arattai Dissemination Tool

Local tool: sends text/images/files to many Arattai chats/groups using a real browser (Playwright).

## Run (any Windows PC)
1. Install Python 3.12/3.13 (tick "Add to PATH").
2. Download this repo as ZIP (Code > Download ZIP), unzip.
3. Double-click `start.bat` -> opens http://localhost:8765
4. Click "Browser kholo / Login", scan QR in the browser that opens.

## Optional: dashboard from GitHub Pages
Enable Settings > Pages > Deploy from branch (main, /root). Open the Pages URL on the PC where
`start.bat` is running; the page talks to http://localhost:8765.

## Security
Never commit `arattai_profile/` (it is your Arattai login). It is in `.gitignore`.
Use a private repo.

## Cloud mode (Railway) - PC/phone ki zarurat nahi
1. Is repo se Railway par naya service banayein (Dockerfile auto-detect hoga).
2. Variables: `ACCESS_TOKEN` = koi lamba password. (`CLOUD`, `AUTOSTART`, `DATA_DIR` Dockerfile me set hain.)
3. Volume add karein, mount path `/data` (login yahin save hota hai, warna har deploy par QR dubara).
4. Settings > Networking > Generate Domain -> ye aapka permanent link.
5. Link kholein, Access token daalein, "Browser kholo / Login" dabayein, QR phone se scan karein.
