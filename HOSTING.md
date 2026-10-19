# Hosting Stemroom

## Current public websites

- Personal website entry: https://tylergriffith.us/stemroom/
- GitHub Pages mirror: https://tyler-the-griffith.github.io/stemroom/
- Repository: https://github.com/tyler-the-griffith/stemroom

These are static entry pages. **The online backend is not deployed yet.**
`docs/room.json` has no room URL until a protected backend is provisioned and
verified. The site explicitly displays that state instead of claiming the room
is online. The full application continues to work on the local host.

The portfolio's top navigation links to `/stemroom/`. The entry page and the
application both have a link back to `https://tylergriffith.us/` in their top bars.
The app's numbered Queue / Stage / Studio navigation stays visible on mobile.

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

## VPS deployment outline

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
