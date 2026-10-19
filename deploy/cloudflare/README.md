# Static frontend and Cloudflare backend

The browser app is served at `https://tylergriffith.us/stemroom/` by WestHost.
HTML, themes, JavaScript, the mixer and AudioWorklet assets are ordinary static
files. `connection.js` selects the backend. API requests, live room WebSockets,
artwork, audio streams and exports go directly to that HTTPS backend.

The computer keeps the authoritative library, collections, mixes, queue,
YouTube downloader and Python processing pipeline. This avoids copying private
library data into Git or maintaining a second, potentially stale song catalog.
Playback still needs the backend to load songs. When it disconnects, the site
stays open at the same URL and reconnects automatically.

## Build and deploy

```
python scripts/build-frontend.py --output /path/to/website/stemroom \
  --backend https://stemroom-origin.tylergriffith.us
```

The script copies only `web/`, writes the backend configuration, and supplies
Apache MIME types for modules and WASM. Deploy those files to the static host.
Use `--runtime` for automatic Quick Tunnel discovery from `backend.json`.
`scripts/online-service.py` checks the tunnel and publishes that tiny public
file over SSH using `STEMROOM_PUBLISH_SSH` and `STEMROOM_PUBLISH_DIR`. It retries
failed announcements. Browser reconnections reread the file, so restarting the
tunnel does not require a website rebuild or DNS changes. Keep `backend.json`
out of Git. This provides a stable website link while retaining Quick Tunnel
availability limits. A named tunnel remains an optional future improvement.

Run the backend with:

```
STEMROOM_PUBLIC_URL=https://tylergriffith.us/stemroom \
STEMROOM_FRONTEND_ORIGINS=https://tylergriffith.us,https://www.tylergriffith.us \
./run.sh
```

CORS allows the named frontend origins, including JSON controls and audio range
requests. It is not authentication: this room remains public with full control
as requested. The canonical public URL makes QR codes point to the website.
Local same-server installs continue working without these variables.

## Optional named tunnel activation

1. Add `tylergriffith.us` to Cloudflare's free plan. Compare the imported DNS
   records against the complete current provider zone, including mail,
   verification, and any subdomains. A quick scan is not a complete backup.
2. Preserve WestHost website records and all email records. Keep mail and FTP
   hostnames DNS-only. Change the domain's
   nameservers only after the records have been reviewed. Domain registration
   and website hosting remain with their existing providers.
3. Create a named tunnel and connect `stemroom-origin.tylergriffith.us` to
   `http://127.0.0.1:8765`. Keep tunnel credentials outside Git.
4. Run the app and tunnel as automatically restarted services. Keep the working
   Quick Tunnel until the new hostname passes checks.
5. Rebuild the static frontend with the permanent backend hostname. Verify the
   live queue, QR link, artwork, audio, downloads, controls and WebSocket sync.
   Restart the tunnel and verify the same hostname reconnects.
6. Point GitHub Pages to `https://tylergriffith.us/stemroom/` using
   `python scripts/set-room.py --public https://tylergriffith.us/stemroom/`.

No Worker route is needed: the browser stays on the WestHost page and connects
to the tunnel separately. The website address survives a backend outage;
processing, library access and shared controls require the computer to be on.

## Verification

Run `node --test tests/*.test.mjs` and
`python -m unittest discover -s tests -v`. Tests use temporary data. For browser
QA, build a separate frontend against a temporary-data server on another port
and include that preview origin in `STEMROOM_FRONTEND_ORIGINS`.

References:
- https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/
- https://developers.cloudflare.com/tunnel/get-started/
