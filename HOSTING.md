# Hosting Stemroom

## Website and backend

- Website: https://tylergriffith.us/stemroom/
- GitHub Pages entry: https://tyler-the-griffith.github.io/stemroom/
- Source: https://github.com/tyler-the-griffith/stemroom

WestHost serves the complete browser interface at `/stemroom/`: the Queue,
Stage, Studio, themes, mixer JavaScript and audio worklet. Visitors stay at that
address, with a top-bar link back to the portfolio. GitHub Pages redirects here.

The Python backend runs on Tyler's computer through a public Cloudflare Tunnel.
It owns the shared queue, library, artwork, audio, lyrics, collections and mixes,
and performs imports, separation and exports. Everyone has full control, as
requested by the owner. There are no separate visitor accounts or private
libraries. Music, models, credentials and the database are excluded from Git.

The static website continues to load while the computer is off. Loading songs,
processing and shared controls need the computer and tunnel to be online. The
page reconnects automatically after a backend outage.

## Deploy the browser interface

```
python scripts/build-frontend.py --output /path/to/portfolio/stemroom \
  --backend https://your-backend-hostname --runtime
```

Deploy that directory using the portfolio's normal Git/WestHost workflow. Do
not copy `docs/` over it: `docs/` is the Pages redirect, not the application.
`connection.js` is public configuration containing only the backend URL.

Configure the backend with:

```
STEMROOM_PUBLIC_URL=https://tylergriffith.us/stemroom \
STEMROOM_FRONTEND_ORIGINS=https://tylergriffith.us,https://www.tylergriffith.us \
./run.sh
```

These settings keep QR codes on the website and allow its cross-origin API and
audio requests. They are not an authentication mechanism. Local same-server
installs work without these settings.

## Cloudflare

A Quick Tunnel currently carries backend traffic. Its URL changes when the
process is restarted. The tunnel service checks that the new backend is ready,
then atomically publishes its public URL to `stemroom/backend.json` on WestHost
over the existing SSH connection. The browser checks this file on startup and
while reconnecting. Visitors keep the same website address after restarts.

Build with `--runtime` to enable this discovery. `backend.json` is deliberately
untracked, contains no credentials, and is not overwritten by normal Git
deployments. If SSH publication fails, the service retries every ten seconds.
Quick Tunnels still have no uptime guarantee; they are not a production SLA.

[Permanent tunnel setup](deploy/cloudflare/README.md) describes moving DNS after
reviewing WestHost's complete zone, creating a named tunnel hostname, and
switching `connection.js`. This is optional: automatic Quick Tunnel discovery
works without changing WestHost DNS or logging into its dashboard. No
Cloudflare Worker or paid server is needed for this split. The permanent tunnel remains dependent on the computer being on.

The helper `./scripts/start-online.sh --public` starts a temporary public
Cloudflare tunnel. The verified binary can live in ignored
`data/tools/cloudflared`, be on PATH, or be set with `CLOUDFLARED`.

## Server operation

On Tyler's installed computer, `stemroom.service` is a systemd user service,
started at login and restarted on failure. Use `systemctl --user status
stemroom` and `journalctl --user -u stemroom` for diagnostics.
`stemroom-tunnel.service` likewise starts at login and restarts the tunnel after
failures. `journalctl --user -u stemroom-tunnel` includes publication status.
Both services run as the existing user; they need the computer awake and a
working network. A logout/reboot requires the user session to start again.
The tunnel publisher uses `scripts/online-service.py` with
`STEMROOM_PUBLISH_SSH` and `STEMROOM_PUBLISH_DIR` set to the existing website.

Keep `data/` backed up separately, including SQLite and versioned audio. Stop
the server or use SQLite's backup API to create a consistent database backup.
Do not run this persistent Python/model pipeline on WestHost shared hosting.
The generic VPS templates under `deploy/` remain optional future examples.

## GitHub Pages

```
python scripts/set-room.py --public https://tylergriffith.us/stemroom/
python scripts/check-site.py
```

The Pages workflow publishes `docs/`. It sends visitors to the website Queue.
Do not put credentials, songs, lyrics, models or private library data in it.
