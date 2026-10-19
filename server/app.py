"""Stemroom web server: REST API, WebSocket room sync, and the static web app.

Run with ./run.sh (binds every interface so guests' phones on the LAN can join).
"""
import asyncio
import hashlib
import json
import re
import shutil
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from . import config, store, catalog
from . import library as lib
from .jobs import Jobs
from .pipeline import lyrics as lyr
from .pipeline import ytdl
from .subpath import SubpathMiddleware

room = store.Room()
clients: set[WebSocket] = set()
client_ids = {}
player_id = None


async def broadcast(msg: dict):
    data = json.dumps(msg)
    dead = []
    for ws in list(clients):
        try:
            await ws.send_text(data)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def state_msg():
    snap = room.snapshot()
    snap["jobs"] = jobs.snapshot()
    snap["playerId"] = player_id
    for sid in jobs.status:  # songs being processed but not in the queue (e.g. library refresh)
        if sid not in snap["songs"]:
            snap["songs"][sid] = store.song_summary(store.read_song(sid))
    return {"type": "state", "state": snap}


_push_lock = asyncio.Lock()


async def push_state(*_):
    async with _push_lock:
        await broadcast(state_msg())


jobs = Jobs(push_state)


@asynccontextmanager
async def lifespan(_app):
    config.SONGS.mkdir(parents=True, exist_ok=True)
    config.EXPORTS.mkdir(parents=True, exist_ok=True)
    jobs.start()
    index_task = lib.index_task = asyncio.create_task(lib.index_existing())
    # Resume anything that was mid-processing when the server stopped.
    for s in store.list_songs():
        if s.get("status") in ("processing", "queued"):
            submit_process(s["id"])
    for sid, args in catalog.replacement(None).items():
        if store.read_song(sid) and sid not in jobs.status:
            fut=jobs.submit('process',sid,args)
            fut.add_done_callback(lambda f:f.exception() if not f.cancelled() else None)
    yield
    index_task.cancel()
    await jobs.stop()


app = FastAPI(title="Stemroom", lifespan=lifespan)
app.add_middleware(SubpathMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=config.FRONTEND_ORIGINS,
                   allow_methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'],
                   allow_headers=['Content-Type', 'Range'],
                   expose_headers=['Content-Range', 'Accept-Ranges', 'Content-Disposition'])


@app.middleware("http")
async def revalidate_static(request, call_next):
    """App files change while developing; make browsers revalidate them (ETag keeps it cheap)."""
    resp = await call_next(request)
    if not request.url.path.startswith(("/media/", "/exports/")):
        resp.headers.setdefault("Cache-Control", "no-cache")
    return resp


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def submit_process(song_id):
    if song_id in jobs.status: return
    store.update_song(song_id, status="processing", error=None)
    fut = jobs.submit("process", song_id, {"quality": room.settings["quality"]})
    fut.add_done_callback(lambda f: f.exception())  # errors are reported via song status


def submit_aux(task, song_id, args, done_msg):
    """Lyrics / refine jobs: tell everyone when finished so open views reload the song."""
    fut = jobs.submit(task, song_id, args)

    async def wait():
        try:
            await fut
            await broadcast({"type": "song", "songId": song_id})
            await broadcast({"type": "toast", "text": done_msg})
        except Exception as e:  # noqa: BLE001
            await broadcast({"type": "toast", "text": f"That didn't work: {e}", "error": True})
    asyncio.create_task(wait())


# ---------------------------------------------------------------- room / queue

@app.get("/api/state")
async def get_state():
    return state_msg()["state"]


@app.get("/api/info")
async def info(request: Request):
    lan_url = f"http://{lan_ip()}:{config.PORT}"
    # HTTPS visitors need the public tunnel's address, not the host's private IP.
    # Proxy headers are trusted only from the local tunnel by Uvicorn's default.
    path = request.scope.get('root_path', '').rstrip('/')
    public_url = (config.PUBLIC_URL or str(request.base_url).rstrip('/')) if request.url.scheme == "https" else lan_url + path
    return {"lanUrl": lan_url, "joinUrl": f"{public_url}/#/join"}


@app.post("/api/search")
async def search(body: dict):
    q = (body.get("q") or "").strip()
    if not q:
        return []
    try:
        return await run_in_threadpool(ytdl.search, q, 8)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"YouTube search failed: {e}")


def _enqueue(song_id, body):
    singers = body.get("singers") or []
    if isinstance(singers, str):
        singers = re.split(r"\s*(?:,|&|\+|\band\b)\s*", singers)
    entry = room.new_entry(song_id, singers, body.get("addedBy"), body.get("mixId"))
    if body.get("playNext"):
        room.queue.insert(0, entry)
    else:
        room.queue.append(entry)
    if room.current is None:
        room.advance()
    room.save()
    return entry


@app.post("/api/queue")
async def add_youtube(body: dict):
    return await lib.youtube(body)


@app.post("/api/upload")
async def add_file(file: UploadFile = File(...), singers: str = Form(""), addedBy: str = Form(""),
                   title: str = Form(""), artist: str = Form(""), collectionId: str = Form("")):
    return await lib.upload(file, dict(singers=singers,addedBy=addedBy,title=title,artist=artist,collectionId=collectionId))


@app.post("/api/library/{song_id}/queue")
async def add_from_library(song_id: str, body: dict):
    if not store.read_song(song_id):
        raise HTTPException(404)
    entry = _enqueue(song_id, body)
    await push_state()
    return entry


@app.patch("/api/queue/{entry_id}")
async def edit_entry(entry_id: str, body: dict):
    e = room.find(entry_id)
    if not e:
        raise HTTPException(404)
    for k in ("singers", "key", "tempo", "guide", "backing"):
        if k in body:
            e[k] = body[k]
    if isinstance(e["singers"], str):
        e["singers"] = [s for s in re.split(r"\s*(?:,|&)\s*", e["singers"]) if s]
    e["key"] = max(-7, min(7, int(e["key"])))
    e["tempo"] = max(0.6, min(1.4, float(e["tempo"])))
    e["guide"] = max(0.0, min(1.0, float(e["guide"])))
    room.save()
    await push_state()
    return e


@app.delete("/api/queue/{entry_id}")
async def remove_entry(entry_id: str):
    if room.current and room.current["id"] == entry_id:
        room.current = None
        room.advance()
    room.queue = [e for e in room.queue if e["id"] != entry_id]
    room.save()
    await push_state()
    return {"ok": True}


@app.post("/api/queue/reorder")
async def reorder(body: dict):
    order = body.get("order") or []
    by_id = {e["id"]: e for e in room.queue}
    new = [by_id.pop(i) for i in order if i in by_id]
    room.queue = new + list(by_id.values())
    room.save()
    await push_state()
    return {"ok": True}


@app.post("/api/queue/{entry_id}/now")
async def play_now(entry_id: str):
    """Put an upcoming entry on stage right away (current one goes back to the front)."""
    e = next((x for x in room.queue if x["id"] == entry_id), None)
    if not e:
        raise HTTPException(404)
    room.queue.remove(e)
    if room.current:
        room.queue.insert(0, room.current)
    room.current = e
    room.stage.update(status="intermission", position=0.0, at=time.time())
    room.save()
    await push_state()
    return e


@app.post("/api/transport")
async def transport(body: dict):
    action = body.get("action")
    if action == "next":
        room.advance()
        room.save()
        await push_state()
    elif action == "previous" and room.history:
        prev = room.history.pop()
        prev.pop("finishedAt", None)
        if room.current:
            room.queue.insert(0, room.current)
        room.current = prev
        room.stage.update(status="intermission", position=0.0)
        room.save()
        await push_state()
    # play / pause / seek / restart are executed by the stage page.
    await broadcast({"type": "transport", **body})
    return {"ok": True}


@app.put("/api/settings")
async def settings(body: dict):
    for k in store.DEFAULT_SETTINGS:
        if k in body:
            room.settings[k] = body[k]
    room.save()
    await push_state()
    return room.settings


# ---------------------------------------------------------------- songs

@app.get("/api/songs")
async def library():
    return [store.song_summary(s) for s in store.list_songs()]


@app.get("/api/songs/{song_id}")
async def song(song_id: str, version: str | None = None):
    s = store.read_song(song_id, version)
    if not s:
        raise HTTPException(404)
    return {
        "song": s,
        "analysis": store.read_part(song_id, "analysis", {}, version),
        "lyrics": store.read_part(song_id, "lyrics", {"lines": []}, version),
        "sections": store.read_part(song_id, "sections", [], version),
        "mix": (catalog.get_mix(song_id) or {}).get("settings", {}),
        "mixes": catalog.mixes(song_id),
        "defaultMixId": (catalog.get_mix(song_id) or {}).get("id"),
    }


@app.get("/api/songs/{song_id}/peaks")
async def peaks(song_id: str, version: str | None = None):
    return store.read_part(song_id, "peaks", {}, version)


@app.put("/api/songs/{song_id}/mix")
async def save_mix(song_id: str, body: dict):
    lib.require_song(song_id)
    lib.ensure_mix(song_id)
    mix = catalog.get_mix(song_id)
    catalog.save_mix(song_id, mix["name"], body, store.read_song(song_id).get("assetVersion","original"), mix["id"])
    await lib.changed()
    return {"ok": True}


@app.put("/api/songs/{song_id}/meta")
async def save_meta(song_id: str, body: dict):
    fields = {k: body[k] for k in ("title", "artist") if k in body}
    if "title" in fields:
        fields["titleOverride"] = fields["title"]
    if "artist" in fields:
        fields["artistOverride"] = fields["artist"]
    store.update_song(song_id, **fields)
    await push_state()
    await lib.changed()
    return {"ok": True}


@app.put("/api/songs/{song_id}/lyrics")
async def save_lyrics(song_id: str, body: dict):
    """Edits from the studio: duet parts, offset, text fixes."""
    cur = store.read_part(song_id, "lyrics", {"lines": []})
    for k in ("lines", "offset", "parts"):
        if k in body:
            cur[k] = body[k]
    store.write_part(song_id, "lyrics", cur)
    await broadcast({"type": "lyrics", "songId": song_id, "lyrics": cur})
    return {"ok": True}


@app.post("/api/songs/{song_id}/lyrics/search")
async def lyrics_search(song_id: str, body: dict):
    s = store.read_song(song_id) or {}
    try:
        return await run_in_threadpool(lyr.search, s.get("artist") or "", s.get("title") or "", body.get("q") or None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Lyrics search failed: {e}")


@app.post("/api/songs/{song_id}/lyrics/use")
async def lyrics_use(song_id: str, body: dict):
    args = {k: body[k] for k in ("lrclibId", "lrc", "plain", "whisper") if body.get(k)}
    submit_aux("lyrics", song_id, args, "Lyrics updated")
    await push_state()
    return {"ok": True}


@app.post("/api/songs/{song_id}/refine")
async def refine(song_id: str, body: dict):
    if body.get("op") not in ("drumsep", "dereverb", "harmonies"):
        raise HTTPException(400)
    song = store.read_song(song_id)
    if not song:
        raise HTTPException(404, "Song not found")
    if song_id in jobs.status or song.get("status") != "ready":
        raise HTTPException(409, "Wait for this song's current job to finish")
    ids = {s["id"] for s in song.get("stems", [])}
    if body.get("stem") not in ids:
        raise HTTPException(400, "Stem not found")
    if body.get("op") == "harmonies":
        if body.get("stem") != "backing" or not {"lead", "backing"} <= ids:
            raise HTTPException(400, "Harmony separation needs lead and backing stems. Use Best quality first.")
        if song.get("harmonyAnalysis", {}).get("version") == 1:
            return {"ok": True, "alreadySeparated": True}
    submit_aux("refine", song_id, {"stem": body["stem"], "op": body["op"]}, "New stems are ready")
    await push_state()
    return {"ok": True}


@app.post("/api/songs/{song_id}/retry")
async def retry(song_id: str):
    submit_process(song_id)
    await push_state()
    return {"ok": True}


@app.post("/api/songs/{song_id}/export")
async def export(song_id: str, body: dict):
    s = store.read_song(song_id)
    if not s:
        raise HTTPException(404)
    fmt = "wav" if body.get("format") == "wav" else "mp3"
    safe = re.sub(r"[^\w\- ]+", "", f"{s.get('artist', '')} - {s.get('title', 'mix')}").strip() or "mix"
    name = f"{safe} (Stemroom mix {time.strftime('%H%M%S')}).{fmt}"
    fut = jobs.submit("export", song_id, {"mix": body.get("mix", {}), "key": body.get("key", 0),
                                          "tempo": body.get("tempo", 1), "file": name})
    await push_state()
    try:
        await fut
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, str(e))
    return {"url": f"/exports/{name}"}


@app.delete("/api/songs/{song_id}")
async def delete_song(song_id: str):
    in_use = any(e["songId"] == song_id for e in room.queue) or (room.current and room.current["songId"] == song_id)
    if in_use:
        raise HTTPException(409, "That song is in the queue")
    if song_id in jobs.status:
        raise HTTPException(409, "That song is still processing")
    catalog.song_delete(song_id)
    shutil.rmtree(store.song_dir(song_id), ignore_errors=True)
    await lib.changed()
    return {"ok": True}


@app.get("/media/{song_id}/{name}")
async def media(song_id: str, name: str, v: str = "original"):
    if not re.fullmatch(r"[\w\-]+\.(opus|flac)", name):
        raise HTTPException(404)
    p = store.asset_dir(song_id, v) / "stems" / name
    if not p.exists():
        raise HTTPException(404)
    media_type = "audio/ogg" if name.endswith(".opus") else "audio/flac"
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if name.endswith(".flac"):
        s = store.read_song(song_id) or {}
        headers["Content-Disposition"] = f'attachment; filename="{s.get("title", song_id)} - {name}"'
    return FileResponse(p, media_type=media_type, headers=headers)


@app.get("/artwork/{song_id}")
async def artwork(song_id: str):
    p = store.song_dir(song_id) / 'artwork.jpg'
    if not p.is_file(): raise HTTPException(404)
    return FileResponse(p, media_type='image/jpeg')


@app.post('/api/player/claim')
async def claim_player(body: dict):
    global player_id
    cid=body.get('clientId')
    if not cid or cid not in client_ids.values(): raise HTTPException(409,'Reconnect before opening the stage')
    if player_id and player_id != cid and not body.get('takeover'):
        raise HTTPException(409,'Another screen is playing. Use its controls, or choose Play on this screen.')
    player_id=cid
    await push_state()
    return {'ok':True}


@app.get("/exports/{name}")
async def exported(name: str):
    p = config.EXPORTS / name
    if "/" in name or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, filename=name)


# ---------------------------------------------------------------- websocket

@app.websocket("/ws")
async def ws(socket_: WebSocket):
    global player_id
    await socket_.accept()
    clients.add(socket_)
    client_ids[socket_]=socket_.query_params.get("clientId")
    await socket_.send_text(json.dumps(state_msg()))
    try:
        while True:
            msg = json.loads(await socket_.receive_text())
            if client_ids.get(socket_) != player_id or not player_id: continue
            if msg.get("type") == "position":
                if not room.current or msg.get("entryId") != room.current["id"]: continue
                # The stage reports playback; remotes use it for progress bars.
                room.stage.update(status=msg.get("status", "playing"), position=msg.get("t", 0),
                                  duration=msg.get("duration", 0), rate=msg.get("rate", 1), at=time.time(),
                                  entryId=msg.get("entryId"))
                await broadcast({"type": "position", **room.stage})
            elif msg.get("type") == "ended":
                if room.current and room.current["id"] == msg.get("entryId"):
                    room.advance()
                    room.save()
                    await push_state()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        clients.discard(socket_)
        cid=client_ids.pop(socket_,None)
        if player_id == cid:
            player_id=None
            room.stage.update(status='stopped',at=time.time())
            await push_state()


@app.get("/")
async def index():
    return FileResponse(config.WEB / "index.html", headers={"Cache-Control": "no-cache"})


lib.configure(room,jobs,push_state,broadcast,_enqueue)
app.include_router(lib.router)

app.mount("/", StaticFiles(directory=config.WEB), name="web")


@app.exception_handler(ValueError)
async def bad_value(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)
