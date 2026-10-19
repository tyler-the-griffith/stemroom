"""YouTube search, metadata and audio download via yt-dlp."""
import re
from urllib.parse import parse_qs, urlparse

import yt_dlp

_JUNK = re.compile(
    r"\s*[\(\[][^\)\]]*(official|video|audio|lyrics?|lyric video|visuali[sz]er|hd|hq|4k|remaster(ed)?|"
    r"mv|m/v|live|explicit|clean)[^\)\]]*[\)\]]",
    re.I,
)
_FEAT = re.compile(r"\s+(ft\.?|feat\.?|featuring)\s+.*$", re.I)


def video_id(url: str):
    """Extract an 11-char YouTube id from the usual URL shapes, or None."""
    url = url.strip()
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    try:
        u = urlparse(url if "://" in url else "https://" + url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
    elif "youtube" in host:
        if u.path.startswith(("/shorts/", "/embed/", "/live/")):
            vid = u.path.split("/")[2]
        else:
            vid = (parse_qs(u.query).get("v") or [""])[0]
    else:
        return None
    return vid if re.fullmatch(r"[\w-]{11}", vid or "") else None


def clean_title(t: str) -> str:
    t = _JUNK.sub("", t or "")
    t = re.sub(r"\s*\|.*$", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–—")


def guess_artist_title(info: dict):
    track, artist = info.get("track"), info.get("artist") or info.get("creator")
    if track and artist:
        return artist.split(",")[0].strip(), clean_title(track)
    title = clean_title(info.get("title") or "")
    m = re.match(r"^(.+?)\s+[-–—]\s+(.+)$", title)
    if m:
        return m.group(1).strip(), _FEAT.sub("", m.group(2)).strip()
    uploader = re.sub(r"(\s*-\s*Topic|VEVO|Official)$", "", info.get("uploader") or info.get("channel") or "", flags=re.I).strip()
    return uploader, title


def search(query: str, limit: int = 8):
    opts = {"quiet": True, "skip_download": True, "extract_flat": "in_playlist", "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    out = []
    for e in res.get("entries") or []:
        vid = e.get("id")
        if not vid:
            continue
        thumbs = e.get("thumbnails") or []
        out.append({
            "id": vid,
            "title": e.get("title"),
            "channel": e.get("channel") or e.get("uploader"),
            "duration": e.get("duration"),
            "thumbnail": thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        })
    return out


def download(vid: str, out_dir, on_progress=None, on_meta=None, attempts=4):
    """Download best audio; returns (path, info).

    YouTube intermittently hands out stream URLs that answer 403, so each retry re-extracts
    to get fresh URLs, and later attempts let yt-dlp pick other player clients and formats.
    """
    import time

    def hook(d):
        if on_progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                on_progress(min(1.0, d.get("downloaded_bytes", 0) / total))

    url = f"https://www.youtube.com/watch?v={vid}"
    base = {
        "quiet": True,
        "noprogress": True,
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "progress_hooks": [hook],
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
    }
    with yt_dlp.YoutubeDL({**base, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if on_meta:
        on_meta(info)

    last = None
    for attempt in range(attempts):
        opts = {**base, "format": "bestaudio/best" if attempt < 2 else "best"}
        if attempt == 1:
            opts["extractor_args"] = {"youtube": {"player_client": ["default", "web_safari"]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return ydl.prepare_filename(info), info
        except yt_dlp.utils.DownloadError as e:
            last = e
            for part in out_dir.glob("source.*"):
                part.unlink(missing_ok=True)
            time.sleep(2 * (attempt + 1))
    raise last
