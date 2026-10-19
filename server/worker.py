"""Heavy lifting, run as a short-lived subprocess per job:  python -m server.worker <task> <song_id> [json]

Each job exits when done so the GPU is released again (the Windows VM needs it free to start).
Progress goes to stdout as lines prefixed with @@ followed by JSON; tqdm bars on stderr are
parsed by the parent for fine-grained percentages.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

from . import config

if config.DEVICE == "cpu":
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TORCH_HOME", str(config.MODELS / "torch"))

from . import store  # noqa: E402
from .pipeline import separate as sep  # noqa: E402


def emit(**ev):
    sys.stdout.write("@@" + json.dumps(ev) + "\n")
    sys.stdout.flush()


class Progress:
    """Maps sub-step progress into the overall 0..1 range for the job."""

    def __init__(self, plan):
        self.plan = plan  # [(key, weight)]
        total = sum(w for _, w in plan)
        acc, self.ranges = 0.0, {}
        for k, w in plan:
            self.ranges[k] = (acc / total, (acc + w) / total)
            acc += w
        self.key = None

    def step(self, key, label):
        self.key = key
        lo, hi = self.ranges[key]
        emit(event="stage", label=label, lo=lo, hi=hi)

    def sub(self, label, a, b):
        """Sub-range inside the current step (used by separation)."""
        lo, hi = self.ranges[self.key]
        emit(event="stage", label=label, lo=lo + (hi - lo) * a, hi=lo + (hi - lo) * b)

    def frac(self, f):
        emit(event="frac", value=f)


# ---------------------------------------------------------------- tasks

def task_process(song_id, args):
    song = store.read_song(song_id)
    d = store.asset_dir(song_id)
    quality = args.get("quality", "best")
    prog = Progress([("download", 6), ("separate", 70 if quality == "best" else 15), ("harmonies", 2), ("encode", 6),
                     ("analyze", 6), ("lyrics", 12)])
    work = d / "work"

    # 1. Source audio
    mix = d / "mix.wav"
    if song["source"]["type"] == "youtube":
        prog.step("download", "Downloading from YouTube")
        from .pipeline import ytdl
        if not [p for p in d.glob("source.*") if not p.name.endswith((".part", ".ytdl"))]:
            def on_meta(info):
                # Show the real title right away, before the (slower) download finishes.
                artist, title = ytdl.guess_artist_title(info)
                store.update_song(song_id, title=song.get("titleOverride") or title, artist=song.get("artistOverride") or artist,
                                  duration=info.get("duration"), thumbnail=song.get("thumbnail") or info.get("thumbnail"),
                                  sourceTitle=info.get("title"), channel=info.get("channel"))
                emit(event="meta")
            ytdl.download(song["source"]["videoId"], d, prog.frac, on_meta)
            song = store.read_song(song_id)
        src = next(d.glob("source.*"))
    else:
        prog.step("download", "Reading your file")
        src = next(d.glob("source.*"))
    sep.to_wav(src, mix)
    import soundfile as sf
    duration = sf.info(mix).duration
    song = store.update_song(song_id, duration=round(duration, 2))

    # 2. Separation
    prog.step("separate", "Separating stems")
    wavs = sep.separate(mix, work, quality, prog.sub)

    harmony_report = None
    if "backing" in wavs:
        from .pipeline import harmonies
        prog.step("harmonies", "Finding overlapping harmony vocals")
        children, harmony_report = harmonies.split(wavs["lead"], wavs["backing"], work / "harmonies")
        ordered = {}
        for sid, path in wavs.items():
            ordered[sid] = path
            if sid == "backing":
                ordered.update(children)
        wavs = ordered

    # 3. Encode + peaks + loudness
    prog.step("encode", "Preparing stems for playback")
    stems = []
    peaks = {}
    loud = {sid: sep.loudness_db(p) for sid, p in wavs.items()}
    raw_peaks = {sid: sep.peaks(p) for sid, p in wavs.items()}
    top = max(float(p.max()) for p in raw_peaks.values()) or 1.0
    for i, (sid, wav) in enumerate(wavs.items()):
        sep.encode(wav, d / "stems", sid)
        name, color = config.STEM_INFO[sid]
        stems.append({"id": sid, "name": name, "color": color, "db": round(float(loud[sid]), 1), "quiet": bool(loud[sid] < -42)})
        if harmony_report and sid == "backing":
            stems[-1]["replacedBy"] = list(harmonies.CHILDREN)
        elif harmony_report and sid in harmonies.CHILDREN:
            stems[-1]["parent"] = "backing"
        peaks[sid] = [int(min(100, v / top * 100 * 1.15)) for v in raw_peaks[sid]]
        prog.frac((i + 1) / len(wavs))
    store.write_part(song_id, "peaks", peaks)
    store.update_song(song_id, stems=stems, quality=quality)
    write_spectrogram(song_id, d / "stems", stems)
    if harmony_report:
        store.write_part(song_id, "harmonies", harmony_report)
        store.update_song(song_id, harmonyAnalysis=harmony_report)

    # 4. Analysis
    prog.step("analyze", "Finding tempo, key and chords")
    from .pipeline import analysis
    rhythm = [wavs["drums"]] if not next(s for s in stems if s["id"] == "drums")["quiet"] else [mix]
    harmony = [wavs[k] for k in ("bass", "guitar", "piano", "other") if k in wavs]
    an = analysis.analyze(rhythm, harmony)
    store.write_part(song_id, "analysis", an)
    store.update_song(song_id, key=an["key"], tempo=an["tempo"])

    # 5. Lyrics (never fatal)
    prog.step("lyrics", "Lining up the lyrics")
    try:
        build_lyrics(song_id, wavs["lead"], duration, an, args)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        store.write_part(song_id, "lyrics", {"source": None, "lines": [], "error": str(e)})
        store.write_part(song_id, "sections", analysis.fallback_sections(duration, an["beats"]))
        store.update_song(song_id, hasLyrics=False, lyricsSource=None)

    sep.cleanup(work)
    # The full-mix wav is only needed while processing; FLAC stems are the archive.
    mix.unlink(missing_ok=True)
    store.update_song(song_id, status="ready", error=None, readyAt=time.time())


def build_lyrics(song_id, vocal_path, duration, an, args):
    from .pipeline import analysis, lyrics

    song = store.read_song(song_id)
    found, source, lrclib_id, loose = None, None, None, False
    if args.get("lrc"):
        found, source = {"syncedLyrics": args["lrc"]}, "user"
    elif args.get("plain"):
        found, source = {"plainLyrics": args["plain"]}, "user"
    elif args.get("lrclibId"):
        found, source = lyrics.get(int(args["lrclibId"])), "lrclib"
    elif not args.get("whisper"):
        found = lyrics.best_match(song.get("artist") or "", song.get("title") or "", duration)
        if not found:
            found = lyrics.best_match(song.get("artist") or "", song.get("title") or "", duration, tolerance=120)
            loose = bool(found)
        source = "lrclib" if found else None
    if found:
        lrclib_id = found.get("id")

    lines, offset = [], 0.0
    aligner = None
    if found and (found.get("syncedLyrics") or found.get("plainLyrics")):
        try:
            aligner = lyrics.Aligner(vocal_path)
        except Exception:  # noqa: BLE001 - alignment is a nicety; even spacing still works
            traceback.print_exc()
        if found.get("syncedLyrics"):
            entries = lyrics.parse_lrc(found["syncedLyrics"])
            if aligner:
                starts = [t for t, txt in entries if txt]
                offset = aligner.estimate_offset(starts, max_shift=90.0 if loose else 4.0)
            lines = lyrics.lines_from_lrc(entries, duration, aligner, offset)
        elif aligner:
            lines = lyrics.lines_from_plain(found["plainLyrics"], duration, aligner)
    if not lines:
        lines = lyrics.lines_from_whisper(vocal_path)
        source = "whisper" if lines else None

    sections = lyrics.sections_from_lines(lines, duration) if lines else analysis.fallback_sections(duration, an["beats"])
    store.write_part(song_id, "lyrics", {"source": source, "lrclibId": lrclib_id, "offset": 0.0,
                                         "autoOffset": offset, "lines": lines})
    store.write_part(song_id, "sections", sections)
    store.update_song(song_id, hasLyrics=bool(lines), lyricsSource=source)


def task_lyrics(song_id, args):
    prog = Progress([("lyrics", 1)])
    prog.step("lyrics", "Lining up the lyrics")
    song = store.read_song(song_id)
    an = store.read_part(song_id, "analysis", {"beats": []})
    d = store.asset_dir(song_id)
    vocal = d / "stems" / ("lead.flac" if (d / "stems" / "lead.flac").exists() else "vocals.flac")
    build_lyrics(song_id, vocal, song["duration"], an, args)


def task_refine(song_id, args):
    stem, op = args["stem"], args["op"]
    if op == "harmonies":
        return task_harmonies(song_id, args)
    prog = Progress([("refine", 9), ("encode", 1)])
    prog.step("refine", "Refining")
    d = store.asset_dir(song_id)
    work = d / "work-refine"
    wav = work / f"{stem}.wav"
    work.mkdir(parents=True, exist_ok=True)
    sep.to_wav(d / "stems" / f"{stem}.flac", wav)
    out = sep.refine(wav, op, work / "out", prog.sub)
    prog.step("encode", "Preparing new stems")
    song = store.read_song(song_id)
    stems = song["stems"]
    peaks = store.read_part(song_id, "peaks", {})
    raw = {sid: sep.peaks(p) for sid, p in out.items()}
    # Keep new waveforms on the same scale as the parent stem's stored peaks.
    parent_raw = float(sep.peaks(wav).max()) or 1.0
    scale = (max(peaks.get(stem) or [100]) / 100) / parent_raw
    pos = next(i for i, s in enumerate(stems) if s["id"] == stem)
    new = []
    for sid, w in out.items():
        sep.encode(w, d / "stems", sid)
        db = sep.loudness_db(w)
        name, color = config.STEM_INFO[sid]
        new.append({"id": sid, "name": name, "color": color, "db": round(float(db), 1), "quiet": bool(db < -50), "parent": stem})
        peaks[sid] = [int(min(100, v * scale * 100)) for v in raw[sid]]
    stems = [s for s in stems if s["id"] not in out]
    if op == "drumsep":
        # Kit pieces replace the drum stem in the mixer; the original stays for downloads.
        stems[pos]["replacedBy"] = list(out)
    stems[pos + 1:pos + 1] = new
    store.write_part(song_id, "peaks", peaks)
    store.update_song(song_id, stems=stems)
    add_to_spectrogram(song_id, d / "stems", list(out))
    sep.cleanup(work)


def task_harmonies(song_id, args):
    """Add complementary backing stems to a prepared song, preserving its originals."""
    from .pipeline import harmonies
    song = store.read_song(song_id)
    if not song or song.get("status") != "ready":
        raise ValueError("Prepare this song before separating its harmonies")
    if song.get("harmonyAnalysis", {}).get("version") == harmonies.VERSION:
        return
    ids = {s["id"] for s in song.get("stems", [])}
    if not {"lead", "backing"} <= ids:
        raise ValueError("This needs separate lead and backing vocals. Prepare the song with Best quality first.")
    d = store.asset_dir(song_id)
    work = d / "work-harmonies"
    prog = Progress([("detect", 2), ("encode", 1)])
    prog.step("detect", "Finding overlapping harmony vocals")
    paths, report = harmonies.split(d / "stems" / "lead.flac", d / "stems" / "backing.flac", work)
    prog.step("encode", "Preparing harmony controls")
    peaks = store.read_part(song_id, "peaks", {})
    parent_peak = float(sep.peaks(d / "stems" / "backing.flac").max()) or 1.0
    scale = (max(peaks.get("backing") or [100]) / 100) / parent_peak
    children = []
    # Publish metadata only after BOTH stems have encoded successfully. The old
    # backing file stays playable/downloadable, including for already open players.
    for sid, path in paths.items():
        sep.encode(path, work / "encoded", sid)
        db = sep.loudness_db(path)
        name, color = config.STEM_INFO[sid]
        children.append({"id": sid, "name": name, "color": color, "db": round(float(db), 1),
                         "quiet": bool(db < -50), "parent": "backing"})
        peaks[sid] = [int(min(100, v * scale * 100)) for v in sep.peaks(path)]
    for path in (work / "encoded").iterdir():
        os.replace(path, d / "stems" / path.name)
    # Preserve metadata changes made while this job was running.
    song = store.read_song(song_id)
    stems = []
    for stem in song["stems"]:
        if stem["id"] in harmonies.CHILDREN:
            continue
        stems.append(stem)
        if stem["id"] == "backing":
            stem["replacedBy"] = list(harmonies.CHILDREN)
            stems.extend(children)
    store.write_part(song_id, "peaks", peaks)
    store.write_part(song_id, "harmonies", report)
    store.update_song(song_id, stems=stems, harmonyAnalysis=report)
    add_to_spectrogram(song_id, d / "stems", list(harmonies.CHILDREN))
    sep.cleanup(work)


def write_spectrogram(song_id, stems_dir, stems):
    """The Studio's separation picture. A nicety: never fail a job over it."""
    from .pipeline import spectrogram
    try:
        store.write_part(song_id, "spectrogram", spectrogram.for_song(stems_dir, stems))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def add_to_spectrogram(song_id, stems_dir, stem_ids):
    from .pipeline import spectrogram
    existing = store.read_part(song_id, "spectrogram")
    if not existing:
        return write_spectrogram(song_id, stems_dir, store.read_song(song_id)["stems"])
    try:
        store.write_part(song_id, "spectrogram", spectrogram.build(stems_dir, stem_ids, existing))
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def task_spectrogram(song_id, args):
    """Backfill for songs prepared before the Studio had a spectrogram."""
    from .pipeline import spectrogram
    prog = Progress([("draw", 1)])
    prog.step("draw", "Drawing the spectrogram")
    song = store.read_song(song_id)
    if not song or song.get("status") != "ready":
        raise ValueError("Prepare this song first")
    store.write_part(song_id, "spectrogram", spectrogram.for_song(store.asset_dir(song_id) / "stems", song["stems"]))


def task_export(song_id, args):
    from .pipeline import export
    prog = Progress([("export", 1)])
    prog.step("export", "Rendering your mix")
    d = store.asset_dir(song_id)
    out = config.EXPORTS / args["file"]
    export.render(d / "stems", args["mix"], float(args.get("key", 0)), float(args.get("tempo", 1)), out)
    emit(event="file", path=str(out))


TASKS = {"process": task_process, "lyrics": task_lyrics, "refine": task_refine, "export": task_export,
         "spectrogram": task_spectrogram}


def main():
    store.catalog.initialize()
    task, song_id = sys.argv[1], sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    try:
        TASKS[task](song_id, args)
        emit(event="done")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        emit(event="error", message=f"{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
