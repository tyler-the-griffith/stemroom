# Hosting Stemroom

## Current public websites

- Personal website entry: https://tylergriffith.us/stemroom/
- GitHub Pages mirror: https://tyler-the-griffith.github.io/stemroom/
- Repository: https://github.com/tyler-the-griffith/stemroom

These are static entry pages. The full application runs on Tyler's computer
behind a public Cloudflare Quick Tunnel. `docs/room.json` points to that
room. Anyone can open it without signing in and has full control of the shared
library and player. Public access was explicitly selected by the owner.

The portfolio's top navigation links to `/stemroom/`. The entry page and the
application both have a link back to `https://tylergriffith.us/` in their top bars.
The app's numbered Queue / Stage / Studio navigation stays visible on mobile.

## Current computer-hosted setup

The computer, Stemroom server, and tunnel process must stay running. No cloud
server has been purchased. Music and the database remain on the computer;
Cloudflare relays HTTPS traffic to visitors. This is one shared room: everyone
can control playback, import, replace or delete songs, edit mixes, and download
stems. There are no separate visitor accounts or private libraries.

Quick Tunnels are temporary: their hostname changes when restarted, there is no
uptime guarantee, and they support up to 200 concurrent in-flight requests. They
are suitable for this interim setup, not a promise of permanent hosting. The
website links must be updated after a tunnel restart. A named Cloudflare Tunnel
and configured domain can provide a stable app hostname later.

To restart after a reboot, first run `./run.sh`. In another terminal:

```bash
./scripts/start-online.sh --public
```

The verified Cloudflare binary can live in ignored `data/tools/cloudflared`, be
on PATH, or be specified with `CLOUDFLARED`. The helper requires an explicit
`--public` choice or individual allowed email addresses. To return to private
access, stop the public tunnel and use
`./scripts/start-online.sh owner@example.com` with the desired addresses.

After the public tunnel prints its HTTPS URL, verify that an anonymous visit
opens the app, then update the entry pages:

```bash
python scripts/set-room.py --public https://your-new-hostname.trycloudflare.com
python scripts/check-site.py
git add docs/room.json
git commit -m "Update public room address"
git push
```

Also copy the updated `docs/` into the portfolio repository's `stemroom/`
directory and deploy that repository. Updating only Pages leaves the portfolio
entry pointing at the previous tunnel. The tunnel is currently started as a
detached process; it is not configured to restart automatically after a reboot.

Cloudflare documentation: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/

## WestHost

The existing portfolio is served on WestHost's StackCP shared hosting platform.
It can host the static entry page. Its Python support does not provide the
persistent ASGI server, WebSockets, and long-running inference worker required
by this application. Do not run background daemons on that shared platform.

WestHost offers VPS plans through Heart Internet with root access. A VPS can run
the backend using CPU inference. The right RAM and CPU allocation must be tested
with the selected models; the lowest-cost website plan is not a sizing guide for
audio separation. GPU processing requires a GPU-capable host, or the existing
local machine. No hosting plan has been purchased by this project.

References:
- https://www.westhost.com/vps/
- https://www.westhost.com/web-hosting/
- https://docs.20i.com/web-hosting/daemons-on-shared-hosting

## Optional future VPS deployment

The repository includes example configuration in `deploy/`. These files are
templates, not proof that a cloud deployment is running.

1. Provision a Linux VPS and supply SSH access. Choose a hostname such as
   `stemroom.tylergriffith.us`; point only that subdomain to the VPS. Keep the
   existing portfolio's DNS and hosting unchanged.
2. Install Python, FFmpeg with rubberband support, Deno, Nginx, and certificate
   tooling. Follow the README to install the app at `/srv/stemroom` with a venv.
3. Create an unprivileged `stemroom` service user and a private writable data
   directory. The systemd example binds to `127.0.0.1:8765`, behind the proxy.
4. Enable authentication at the HTTPS reverse proxy before exposing the app.
   The Nginx example uses a password file created with `htpasswd`; create it on
   the server and never commit it. Every admitted visitor can control the shared
   room, import songs, and download stems. This is not a multi-tenant service.
5. Obtain a valid TLS certificate for the subdomain and configure Nginx. It must
   forward WebSocket upgrades, the host, and the original HTTPS protocol.
6. Transfer the existing `data/` separately if the current library is wanted
   online. Stop the app during transfer, or take a consistent database backup.
   Verify file hashes and available disk space; never upload the library to Git.
7. Verify authenticated page loading, API calls, WebSocket state, audio playback,
   import processing, downloads, and the QR join link from a second device.
8. Set `docs/room.json` to the verified HTTPS root URL and an accurate availability
   note. Commit and push to update Pages, then copy `docs/` into the portfolio's
   `stemroom/` directory and deploy the portfolio through its usual process.

The backend serves its frontend on the same HTTPS origin. This keeps streaming,
downloads, worklets, authentication, and WebSockets together. GitHub Pages and
the portfolio provide stable entry links; the actual app runs at the backend's
hostname. This avoids exposing tokens in static JavaScript or introducing
cross-origin authentication workarounds.

## Updating the website

```bash
python scripts/build-site.py
python scripts/check-site.py
```

The Pages workflow publishes only `docs/`. Its assets use relative paths so the
same directory also works at the portfolio's `/stemroom/` path. No API keys,
passwords, model files, or music belong in this directory.
