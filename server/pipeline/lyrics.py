"""Lyrics: fetch synced lyrics from LRCLIB, then time every word against the lead vocal.

Word timing uses torchaudio's MMS forced aligner (wav2vec2) when we know the words, and
falls back to Whisper transcription when no lyrics could be found.
"""
import re
import unicodedata

import httpx
import numpy as np

from .. import config

LRCLIB = "https://lrclib.net/api"
UA = {"User-Agent": "Stemroom karaoke (local party app)"}
_TS = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


# ---------------------------------------------------------------- LRCLIB

def _get_json(client, url, params=None, tries=4):
    """LRCLIB occasionally answers 503 under load; back off and retry."""
    import time
    for i in range(tries):
        r = client.get(url, params=params)
        if r.status_code in (429, 502, 503, 504) and i < tries - 1:
            time.sleep(1.5 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()


def search_raw(artist: str, title: str, query: str | None = None):
    params = {"q": query} if query else {"track_name": title, "artist_name": artist}
    with httpx.Client(timeout=15, headers=UA) as c:
        res = _get_json(c, f"{LRCLIB}/search", params)
        if not res and not query:
            res = _get_json(c, f"{LRCLIB}/search", {"q": f"{artist} {title}"}) or []
    return res


def search(artist: str, title: str, query: str | None = None):
    return [{
        "id": x["id"], "artist": x.get("artistName"), "title": x.get("trackName"),
        "album": x.get("albumName"), "duration": x.get("duration"),
        "synced": bool(x.get("syncedLyrics")), "plain": bool(x.get("plainLyrics")),
        "instrumental": x.get("instrumental"),
    } for x in search_raw(artist, title, query)]


def get(lrclib_id: int):
    with httpx.Client(timeout=15, headers=UA) as c:
        return _get_json(c, f"{LRCLIB}/get/{lrclib_id}")


def best_match(artist, title, duration, tolerance=15):
    """Candidate with synced lyrics and the closest duration.

    Music videos often run longer than the album track (intros, skits), so with a larger
    `tolerance` we also accept a shorter timed version and let alignment find the offset.
    """
    scored = []
    for c in search_raw(artist, title):
        if c.get("instrumental") or not (c.get("syncedLyrics") or c.get("plainLyrics")):
            continue
        dd = abs((c.get("duration") or 0) - duration)
        if dd > tolerance or (tolerance > 15 and not c.get("syncedLyrics")):
            continue
        scored.append((0 if c.get("syncedLyrics") else 1, dd, c))
    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[1]))
    return scored[0][2]


def parse_lrc(text: str):
    """Returns [(time, text)] including empty lines, which mark stanza breaks."""
    out = []
    for raw in text.splitlines():
        stamps = _TS.findall(raw)
        if not stamps:
            continue
        lyric = _TS.sub("", raw).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), lyric))
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------- alignment

def _norm_word(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return re.sub(r"[^a-z']", "", w.encode("ascii", "ignore").decode())


class Aligner:
    """MMS forced aligner over the lead vocal; emissions are computed once for the song."""

    def __init__(self, vocal_path):
        import librosa
        import torch
        import torchaudio

        self.torch = torch
        bundle = torchaudio.pipelines.MMS_FA
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = bundle.get_model(with_star=False).to(self.device).eval()
        self.tokenizer = bundle.get_tokenizer()
        self.aligner = bundle.get_aligner()
        y, _ = librosa.load(str(vocal_path), sr=bundle.sample_rate, mono=True)
        self.sr = bundle.sample_rate
        self.duration = len(y) / self.sr
        chunk = self.sr * 20
        ems = []
        with torch.inference_mode():
            for i in range(0, len(y), chunk):
                seg = torch.from_numpy(y[i:i + chunk]).float()[None].to(self.device)
                if seg.shape[1] < 400:
                    continue
                em, _ = self.model(seg)
                ems.append(em[0].cpu())
        self.emission = torch.cat(ems)
        self.fps = self.emission.shape[0] / self.duration
        # Vocal activity envelope (10 ms frames) for offset estimation.
        hop = self.sr // 100
        n = len(y) // hop
        self.activity = np.sqrt(np.mean(y[: n * hop].reshape(n, hop) ** 2, axis=1))

    def align(self, words, start, end):
        """Align the words inside [start, end]; returns [(start, end)] or None."""
        toks = [_norm_word(w) for w in words]
        idx = [i for i, t in enumerate(toks) if t]
        if not idx:
            return None
        f0 = max(0, int(start * self.fps))
        f1 = min(self.emission.shape[0], int(end * self.fps))
        em = self.emission[f0:f1]
        tokens = self.tokenizer([toks[i] for i in idx])
        if em.shape[0] < sum(len(t) for t in tokens) * 1.2 + 2:
            return None
        try:
            spans = self.aligner(em, tokens)
        except Exception:
            return None
        times = [None] * len(words)
        for k, i in enumerate(idx):
            sp = spans[k]
            times[i] = ((f0 + sp[0].start) / self.fps, (f0 + sp[-1].end) / self.fps)
        _fill_gaps(times, start, end)
        return times

    def _offset_score(self, act, line_starts, off):
        score = 0.0
        for t in line_starts:
            i = int((t + off) * 100)
            if 30 <= i < len(act) - 60:
                score += act[i:i + 60].mean() - act[i - 30:i].mean()
        return score

    def estimate_offset(self, line_starts, max_shift=4.0):
        """Shift (s) that best lines up LRC line starts with vocal onsets."""
        act = self.activity / (self.activity.max() + 1e-9)
        base = self._offset_score(act, line_starts, 0.0)
        coarse = np.arange(-max_shift, max_shift + 0.01, 0.25 if max_shift > 8 else 0.05)
        best = max(coarse, key=lambda o: self._offset_score(act, line_starts, o))
        if max_shift > 8:
            fine = np.arange(best - 0.3, best + 0.31, 0.05)
            best = max(fine, key=lambda o: self._offset_score(act, line_starts, o))
        best_score = self._offset_score(act, line_starts, best)
        # Only move when it is clearly better than leaving LRC timing alone.
        if max_shift <= 8 and best_score < base * 1.15 + 0.05:
            return 0.0
        return round(float(best), 2)


def _fill_gaps(times, start, end):
    """Interpolate words that had no alignable letters (numbers, symbols)."""
    n = len(times)
    for i in range(n):
        if times[i] is None:
            prev_end = times[i - 1][1] if i > 0 and times[i - 1] else start
            j = i
            while j < n and times[j] is None:
                j += 1
            next_start = times[j][0] if j < n else end
            step = (next_start - prev_end) / (j - i + 1)
            for k in range(i, j):
                times[k] = (prev_end + step * (k - i), prev_end + step * (k - i + 1))


def _even(words, start, end):
    """Spread words over a line, weighted by length."""
    weights = [max(1, len(w)) for w in words]
    total = sum(weights)
    t, out = start, []
    for w in weights:
        d = (end - start) * w / total
        out.append((t, t + d))
        t += d
    return out


# ---------------------------------------------------------------- building lines

def _split_words(text):
    return [w for w in text.split() if w]


def lines_from_lrc(entries, duration, aligner, offset=0.0):
    """Timed lines with word timing; empty LRC lines become stanza breaks."""
    lines, breaks = [], set()
    content = [(t + offset, txt) for t, txt in entries]
    for k, (t, txt) in enumerate(content):
        if not txt:
            breaks.add(len(lines))
            continue
        nxt = next((c[0] for c in content[k + 1:]), duration)
        lines.append({"start": t, "next": nxt, "text": txt})
    out = []
    for i, ln in enumerate(lines):
        words = _split_words(ln["text"])
        # A line rarely lasts longer than ~8 s of singing even if the next one is far away.
        end = min(ln["next"], ln["start"] + max(2.0, 0.55 * len(words) + 1.5), duration)
        times = aligner.align(words, max(0, ln["start"] - 0.4), end) if aligner else None
        if not times:
            times = _even(words, ln["start"], end - 0.15)
        out.append(_line(words, times, stanza_break=i in breaks))
    return out


def lines_from_plain(text, duration, aligner):
    """No timestamps: align the whole lyric sheet against the full song."""
    raw = [l.strip() for l in text.splitlines()]
    words, owners, breaks, li = [], [], set(), 0
    texts = []
    for l in raw:
        if not l:
            breaks.add(li)
            continue
        ws = _split_words(l)
        texts.append(ws)
        words += ws
        owners += [li] * len(ws)
        li += 1
    times = aligner.align(words, 0, duration) if aligner else None
    if not times:
        return []
    out, k = [], 0
    for i, ws in enumerate(texts):
        out.append(_line(ws, times[k:k + len(ws)], stanza_break=i in breaks))
        k += len(ws)
    return out


def lines_from_whisper(vocal_path):
    import librosa
    import torch
    from faster_whisper import WhisperModel

    cuda = torch.cuda.is_available()
    model = WhisperModel(config.WHISPER_MODEL, device="cuda" if cuda else "cpu",
                         compute_type="float16" if cuda else "int8",
                         download_root=str(config.MODELS / "whisper"))
    audio, _ = librosa.load(str(vocal_path), sr=16000, mono=True)
    segs, _ = model.transcribe(audio, word_timestamps=True, vad_filter=False,
                               condition_on_previous_text=False)
    out, prev_end = [], 0.0
    for s in segs:
        if not s.words:
            continue
        words = [w.word.strip() for w in s.words]
        times = [(w.start, w.end) for w in s.words]
        out.append(_line(words, times, stanza_break=s.start - prev_end > 4))
        prev_end = s.end
    return out


def _line(words, times, stanza_break=False):
    ws = [{"t": w, "s": round(float(a), 3), "e": round(float(b), 3)} for w, (a, b) in zip(words, times)]
    return {
        "start": ws[0]["s"] if ws else 0.0,
        "end": ws[-1]["e"] if ws else 0.0,
        "text": " ".join(words),
        "words": ws,
        "br": bool(stanza_break),
        "singer": None,
    }


# ---------------------------------------------------------------- sections

def _norm_line(t):
    return re.sub(r"[^a-z ]", "", unicodedata.normalize("NFKD", t.lower())).strip()


def sections_from_lines(lines, duration):
    """Group lines into stanzas; stanzas whose text repeats are the chorus."""
    if not lines:
        return []
    stanzas, cur = [], []
    for i, ln in enumerate(lines):
        gap = ln["start"] - lines[i - 1]["end"] if i else 0
        if cur and (ln.get("br") or gap > 3.0):
            stanzas.append(cur)
            cur = []
        cur.append(i)
    stanzas.append(cur)

    keys = [tuple(_norm_line(lines[i]["text"]) for i in st) for st in stanzas]
    counts = {}
    for st in keys:
        for l in set(st):
            counts[l] = counts.get(l, 0) + 1
    out, verse, t = [], 0, 0.0
    first = lines[stanzas[0][0]]["start"]
    if first > 4:
        out.append({"label": "Intro", "start": 0.0, "end": first})
    for k, st in enumerate(stanzas):
        start = lines[st[0]]["start"]
        end = lines[st[-1]]["end"]
        repeated = sum(1 for l in keys[k] if counts.get(l, 0) >= 2) / max(1, len(keys[k]))
        if repeated >= 0.6 and len(st) >= 2:
            label = "Chorus"
        else:
            verse += 1
            label = f"Verse {verse}"
        if out and start - out[-1]["end"] > 8:
            out.append({"label": "Break", "start": out[-1]["end"], "end": start})
        if out:
            out[-1]["end"] = start
        out.append({"label": label, "start": start, "end": end})
    # Fold tiny stanzas (a lone tag line, an ad-lib) into the section before them.
    merged = []
    for s in out:
        tiny = s["end"] - s["start"] < 7 and s["label"] not in ("Intro", "Break")
        same = merged and s["label"] == "Chorus" == merged[-1]["label"]
        if merged and (same or (tiny and merged[-1]["label"] != "Intro")):
            merged[-1]["end"] = s["end"]
        else:
            merged.append(s)
    out = merged
    n = 0
    for s in out:  # renumber verses after merging
        if s["label"].startswith("Verse"):
            n += 1
            s["label"] = f"Verse {n}"
    last_end = out[-1]["end"]
    if duration - last_end > 4:
        out.append({"label": "Outro", "start": last_end, "end": duration})
    else:
        out[-1]["end"] = duration
    for s in out:
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    for st in stanzas:  # stanza index on lines lets the UI alternate duet parts by stanza
        for i in st:
            lines[i]["stanza"] = stanzas.index(st)
    for s in out:
        s["chorus"] = s["label"] == "Chorus"
    return out
