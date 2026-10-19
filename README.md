# Stemroom

Party karaoke built on stem separation. Paste a YouTube link (or search, or drop a file), and Stemroom splits the song into lead vocals, backing vocals, drums, bass, guitar, piano and everything else, finds synced lyrics, times every word against the isolated vocal, and puts it on a big screen with live key, speed and guide-vocal controls. Guests add songs from their phones.

[Website](https://tylergriffith.us/stemroom/) · [GitHub Pages](https://tyler-the-griffith.github.io/stemroom/) · [Source](https://github.com/tyler-the-griffith/stemroom)

## Website and online app

The website in `docs/` is Stemroom's public entry point, hosted on the portfolio
and mirrored on GitHub Pages. **Open Stemroom** launches the full app through a
protected HTTPS tunnel to the host computer. The computer and tunnel must remain
running. A tunnel restart changes its URL, which must be updated in both entry
pages. GitHub Pages cannot run
Python, FFmpeg, model inference, or the shared library database.

The backend can run on a computer with a protected HTTPS tunnel. The computer
must stay powered on and connected. The tunnel must protect the app before it is
published: the app itself is a shared room and has no account or role system.
See [online hosting](HOSTING.md) for operation and access details.

Only source, tests, documentation, and the public website belong in this
repository. Songs, cover files, lyrics, mixes, party history, exports, model
weights, virtual environments, credentials, and backups stay in ignored local
storage. Back up `data/` separately to preserve the library.

## Run it

Follow **Setup from scratch** below once, then:

```bash
./run.sh
```

Then open <http://localhost:8765>. The server listens on every interface so phones on the same Wi-Fi can join via the QR code on the Queue page.

For phones on the same Wi-Fi, allow TCP port 8765 through the host firewall.
With UFW, restrict it to your actual local subnet; for example, for a network
using `192.168.1.x`:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp
```

A protected outbound tunnel does not need an inbound router port-forward.

Environment knobs:

| Variable | Default | |
|---|---|---|
| `STEMROOM_PORT` | `8765` | |
| `STEMROOM_DEVICE` | `auto` | `cpu` keeps the GPU completely free (see below) |
| `STEMROOM_DATA` | `./data` | songs, models, exports |

### GPU use

Separation runs in a short-lived worker process per song, keeping model loading
out of the web server. NVIDIA CUDA can accelerate processing; CPU mode is slower.
If another workload or virtual machine needs exclusive use of the GPU, wait for
processing to finish or start Stemroom with `STEMROOM_DEVICE=cpu`.

## How it works

```
Browser (any device)                       Server (FastAPI, ./server)
──────────────────────                     ─────────────────────────────────────────────
#/        Queue (host)   ─┐                REST + WebSocket room state (queue, now playing)
#/join    Guests' phones ─┼─ WebSocket ──▶ jobs.py: one worker at a time (one GPU)
#/studio  Mixer, lyrics  ─┤                   worker.py process <song>
#/stage   Big screen  ◀──┘ plays audio        1. yt-dlp download / uploaded file → wav
          engine.js: all stems → one          2. BS-RoFormer: vocals | instrumental
          Signalsmith Stretch node            3. Mel-RoFormer karaoke: lead | backing
          (key + speed, sample-synced)        4. HTDemucs 6s: drums, bass, guitar, piano, other
          → per-stem gain/pan/tone/FX         5. FLAC (download) + Opus (streaming), peaks
                                              6. librosa: tempo, key, chords
                                              7. LRCLIB synced lyrics → MMS forced alignment
                                                 per word on the lead vocal (Whisper fallback)
```

- **The stage is the player.** Only the page showing `#/stage` makes sound; every other page is a remote. Changes from the Queue, Studio or a phone (key, speed, guide vocal, backing vocals, mix, duet parts) apply live.
- **Key and speed** use [Signalsmith Stretch](https://signalsmith-audio.co.uk/code/stretch/) in an AudioWorklet. All stems go through one multichannel node, so they never drift apart. "Keep voices natural" turns on formant compensation.
- **Guide vocal** is how much of the original lead singer stays in (0% is pure karaoke). It's set per singer in the queue.
- **Duets.** Put two or three names in "Who's singing" (`Sam & Lee`). By default, verses alternate between singers and choruses are sung together. In Studio → Lyrics & duet you can reassign any line, swap every line, or use other presets. Each singer gets their own lyric colour on stage.
- **Lyrics.** These come from [LRCLIB](https://lrclib.net) (matched by song length). Line timings are auto-shifted to fit this recording, then every word is aligned against the isolated lead vocal. If the lyrics are wrong, search again, paste your own (plain or LRC), or transcribe the vocal with Whisper from the Studio.
- **Studio extras.** These include per-stem reverb, echo and tone, splitting drums into kick/snare/toms/hi-hat/ride/crash, making a dry (de-reverbed) copy of the lead vocal, looping a section, and exporting the mix (levels, pan, key, speed) as MP3 or WAV.

### Overlapping harmonies

Best-quality imports automatically divide the backing stem into **Overlapping
harmonies** and **Other backing vocals**. For older songs, select Backing vocals
in Studio and choose **Separate overlapping harmonies**. This uses activity in
the isolated lead recording, with short crossfades at phrase boundaries. It does
not identify musical intervals or individual singers; vocal-separation bleed can
also be included. Original audio, covers and saved mixes are preserved.

**Harmonies follow lead** is enabled by default. The overlapping stem uses the
lead's volume setting and, in karaoke playback, its Guide vocal percentage.
Other backing vocals retain their own level. These are matching gain settings,
not loudness normalization. Uncheck the option for an independent harmony fader;
solo the stem to audition it with Karaoke preview off. Save the choice in any
named mix and use **Apply to current performance** to update an existing turn.
Both backing stems obey Backing vocals on/off. An already loaded Stage keeps its
current stems until the next performance or page refresh.

## Setup from scratch

```bash
git clone https://github.com/tyler-the-griffith/stemroom.git
cd stemroom
python3 -m venv .venv
.venv/bin/pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
.venv/bin/pip install -r requirements.txt
```

Python 3.12 is a practical starting point. Install matching PyTorch and
TorchAudio versions for your hardware; the command above uses NVIDIA CUDA 12.8.
For CPU-only use, replace `cu128` with `cpu` in that command and run with
`STEMROOM_DEVICE=cpu`.

On Python 3.14, the separator's pinned `beartype<0.19` is incompatible. After
installing the requirements, the working local setup uses this explicit override:

```bash
.venv/bin/pip install --no-deps 'beartype>=0.22'
```

This deliberately overrides an upstream dependency constraint. The application
has been run with Python 3.14, audio-separator 0.47.1, torch/torchaudio 2.11.0,
and beartype 0.23.1; a clean installation on every platform has not been verified.

System packages: `ffmpeg` (with librubberband for exports), `deno` (yt-dlp needs a JS runtime for YouTube). The models (~1.5 GB for separation, ~1.2 GB for word alignment, ~1.6 GB for Whisper) download into `data/models` on first use.

The browser libraries (Preact, htm, qrcode-generator, Signalsmith Stretch) are vendored in `web/vendor`, so there's no build step.

## Notes

- Use recordings you have permission to process and share. Imports and separated stems stay on the backend's disk; people granted access to the online room can access that library.
- Every processed song is cached in `data/songs/<id>`. Reuse it from All songs or a collection without reprocessing.

## Library, collections and mixes

Use **Theme** in the top bar to choose Stemroom Classic, Olive Garden Feast,
Autumn Harvest, or Fiery Ocean. The choice applies to every view, is saved on that
browser, and syncs between its open tabs. Album covers keep their original colors.

The numbered Queue / Stage / Studio navigation stays visible across views and in
fullscreen. Opening Stage and tapping its playback button designates that screen
as the player. Other screens can control it from Queue. Navigating within the app
does not stop Stage audio. Starting a Studio preview pauses Stage; start Stage
again when you are ready to resume the performance.

**All songs** contains every imported song, including songs being prepared or
needing a retry. Create collections and use **Collections** on a song card to add
it to several collections. Each collection remembers its preferred mix for each
song. Move songs earlier to change their order. Removing a collection or a song's
membership does not delete its audio, artwork or mixes. Collection selection is
remembered on each device.

Studio provides **Save**, **Save as new**, **Duplicate** and **Set as default**.
Mixes include stem levels, mute/solo, pan, tone, effects, master level, key, speed,
guide and backing vocals, and duet assignments. Lyrics text and timing remain
shared for a source version. Unsaved mix drafts stay on the current browser.
Queue entries capture the selected mix and source version. Saving a mix does not
change an already queued performance; **Apply to current performance** is explicit.

Imports check full SHA-256 file hashes and YouTube IDs globally. A duplicate dialog
lets you reuse the song, replace its source, or cancel. Local spectral comparisons
also suggest likely matches across encodings; these suggestions require a choice,
and **Different recording — keep both** is available. This conservative matcher
can miss edited recordings or recordings with different introductions.

Replacement processing uses a separate directory. Only a successful result becomes
the song's new source version. Previous audio remains available to queued entries;
failed replacements leave the original playable. Saved mixes are marked for review
after a replacement. Existing cover URLs and embedded artwork are preserved. File
uploads can extract embedded artwork (or the first video frame).

### Storage and migration

On first start, Stemroom creates `data/catalog.sqlite3` and imports existing song
IDs, the queue and the old per-song mix as **Default**, without reprocessing audio.
SQLite stores collections, memberships, mixes, identities, source versions and room
state. Song/analysis/lyrics JSON sidecars remain available to subprocess workers;
`room.json` is also written for recovery. Prepared stems stay on disk. Replacement
media is under `songs/<id>/versions/<version>/`; versioned media URLs prevent stale
browser caches. Keep the entire `data` directory when backing up, including the
SQLite database (stop the server or use SQLite's backup API for a consistent copy).

Run the isolated integration tests with `.venv/bin/python -m unittest discover -s tests -v`.
They use temporary data and do not touch the live library or run separation models.


## Development

The Python API is in `server/`, the Preact app in `web/`, and the GitHub Pages
entry point in `docs/`. No frontend package installation is needed.

```bash
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
node --test tests/*.test.mjs
python scripts/build-site.py
python scripts/check-site.py
```

The tests use temporary data and synthesized test signals, without downloading
models. FFmpeg is required. GitHub Actions runs these checks on pushes and pull
requests. The Pages workflow publishes `docs/` after website changes on `main`.
See [THIRD_PARTY.md](THIRD_PARTY.md) for bundled dependency notices.
