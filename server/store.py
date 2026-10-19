"""On-disk song library and the party room state (queue, now playing, settings)."""
import json
import os
import time
import uuid
from pathlib import Path

from . import config, catalog


def _atomic_write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


# ---------------------------------------------------------------- songs

def song_dir(song_id: str) -> Path:
    if "/" in song_id or ".." in song_id:
        raise ValueError("bad song id")
    return config.SONGS / song_id


def asset_dir(song_id, version=None):
    if version is None:
        version = (read_song(song_id) or {}).get("assetVersion", "original")
    if not version or version == "original": return song_dir(song_id)
    if not isinstance(version, str) or not version.isalnum(): raise ValueError("Bad source version")
    return song_dir(song_id) / "versions" / version


def read_song(song_id: str, version=None):
    current = _read(song_dir(song_id) / "song.json")
    if version and current and version != current.get("assetVersion", "original"):
        old = catalog.version(song_id, version)
        if old:
            # Title and cover are library metadata, shared by all source versions.
            for k in ("title", "artist", "thumbnail"): old[k] = current.get(k)
        return old
    return current


def write_song(song_id: str, data):
    _atomic_write(song_dir(song_id) / "song.json", data)
    catalog.upsert(data)


def update_song(song_id: str, **fields):
    song = read_song(song_id) or {"id": song_id}
    song.update(fields)
    write_song(song_id, song)
    return song


def read_part(song_id: str, name: str, default=None, version=None):
    """analysis / lyrics / mix / peaks json files next to song.json."""
    return _read(asset_dir(song_id, version) / f"{name}.json", default)


def write_part(song_id: str, name: str, data):
    _atomic_write(asset_dir(song_id) / f"{name}.json", data)


def list_songs():
    out = []
    if not config.SONGS.exists():
        return out
    for d in config.SONGS.iterdir():
        s = _read(d / "song.json")
        if s:
            out.append(s)
    out.sort(key=lambda s: s.get("created", 0), reverse=True)
    return out


def song_summary(song):
    if not song:
        return None
    keys = ("id", "title", "artist", "duration", "thumbnail", "status", "error",
            "source", "quality", "created", "key", "tempo", "hasLyrics", "lyricsSource", "assetVersion", "replacementError")
    return {k: song.get(k) for k in keys}


# ---------------------------------------------------------------- room

DEFAULT_SETTINGS = {
    "autoAdvance": True,
    "countdown": 10,
    "defaultGuide": 0.15,
    "quality": "best",       # best | fast
    "naturalVoice": True,    # formant compensation when changing key
}


class Room:
    def __init__(self):
        self.path = config.DATA / "room.json"
        catalog.initialize()
        for song in list_songs():
            catalog.upsert(song)
            if not catalog.mixes(song['id']):
                mix = read_part(song['id'], 'mix', {})
                mix.setdefault('performance', {'key':0, 'tempo':1, 'guide':0.15, 'backing':True})
                lines = read_part(song['id'], 'lyrics', {}).get('lines', [])
                mix.setdefault('duet', [line.get('singer') for line in lines])
                catalog.save_mix(song['id'], 'Default', mix, song.get('assetVersion','original'))
        data = catalog.document('room') or _read(self.path, {}) or {}
        self.settings = {**DEFAULT_SETTINGS, **data.get("settings", {})}
        self.queue = data.get("queue", [])
        self.current = data.get("current")
        self.history = data.get("history", [])
        for entry in self.queue + self.history + ([self.current] if self.current else []):
            if 'mixSnapshot' not in entry:
                mix = catalog.get_mix(entry['songId'])
                entry.update(mixId=mix['id'] if mix else None, mixName=mix['name'] if mix else 'Default',
                             mixSnapshot=mix['settings'] if mix else {},
                             assetVersion=(read_song(entry['songId']) or {}).get('assetVersion','original'))
        self.save()
        # Transport state is reported live by the stage and not persisted.
        self.stage = {"status": "idle", "position": 0.0, "duration": 0.0, "rate": 1.0, "at": time.time()}

    def save(self):
        data = {"settings": self.settings, "queue": self.queue,
                "current": self.current, "history": self.history[-100:]}
        catalog.document('room', data)
        _atomic_write(self.path, data)

    def new_entry(self, song_id, singers, added_by, mix_id=None):
        singers = [s.strip() for s in singers if s and s.strip()][:4] or ["Someone"]
        mix = catalog.get_mix(song_id, mix_id)
        if mix_id and not mix: raise ValueError("Mix not found")
        if not mix:
            mix = catalog.save_mix(song_id, 'Default', {'performance': {'guide': self.settings['defaultGuide']}},
                                   (read_song(song_id) or {}).get('assetVersion','original'))
        performance = mix['settings'].get('performance', {})
        return {
            "mixId": mix['id'], "mixName": mix['name'], "mixSnapshot": mix['settings'],
            "assetVersion": (read_song(song_id) or {}).get('assetVersion','original'),
            "id": uuid.uuid4().hex[:10],
            "songId": song_id,
            "singers": singers,
            "addedBy": (added_by or "").strip()[:40],
            "addedAt": time.time(),
            "key": performance.get("key",0),
            "tempo": performance.get("tempo",1),
            "guide": performance.get("guide", self.settings["defaultGuide"]),
            "backing": performance.get("backing",True),
        }

    def find(self, entry_id):
        if self.current and self.current["id"] == entry_id:
            return self.current
        for e in self.queue:
            if e["id"] == entry_id:
                return e
        return None

    def advance(self):
        """Move the current entry to history and start the next one."""
        if self.current:
            self.history.append({**self.current, "finishedAt": time.time()})
        self.current = self.queue.pop(0) if self.queue else None
        self.stage.update(status="intermission" if self.current else "idle", position=0.0, at=time.time())

    def snapshot(self):
        ids = {e["songId"] for e in self.queue}
        if self.current:
            ids.add(self.current["songId"])
        ids |= {e["songId"] for e in self.history[-10:]}
        songs = {}
        for sid in ids:
            s = read_song(sid)
            if s:
                songs[sid] = song_summary(s)
        return {
            "settings": self.settings,
            "queue": self.queue,
            "current": self.current,
            "history": self.history[-10:],
            "stage": self.stage,
            "songs": songs,
        }
