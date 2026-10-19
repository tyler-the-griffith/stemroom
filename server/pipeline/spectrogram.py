"""Small per-stem mel spectrograms for the Studio's "how this song came apart" view.

Every stem is drawn on the same time and pitch grid and the same loudness scale, so the
browser can colour each cell by the stem that owns it, show one stem in place over the
mix, or stack the stems as layers that still line up.
"""
import base64

import numpy as np

VERSION = 1
COLS = 640          # time columns for the whole song
BANDS = 80          # mel bands, low to high
SR = 22050
FMAX = 11000
RANGE_DB = 70.0     # quietest level drawn, below the loudest cell of the full mix


def _mel(path):
    import librosa

    y, _ = librosa.load(str(path), sr=SR, mono=True)
    S = librosa.feature.melspectrogram(y=y, sr=SR, n_fft=2048, hop_length=512, n_mels=BANDS, fmax=FMAX)
    n = S.shape[1]
    if n < COLS:
        S = np.pad(S, ((0, 0), (0, COLS - n)))
        n = COLS
    # Average power into COLS equal time slices.
    edges = np.linspace(0, n, COLS + 1).astype(int)
    return np.stack([S[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)


def _encode(S, ref):
    db = 10 * np.log10(S / ref + 1e-12)
    v = np.clip((db + RANGE_DB) / RANGE_DB, 0, 1)
    # Row 0 is the highest band so the browser can draw the bytes top to bottom.
    return base64.b64encode((v[::-1] * 255).round().astype(np.uint8).tobytes()).decode()


def build(stems_dir, stem_ids, existing=None):
    """Spectrograms for stem_ids. Reuses existing["ref"] so stems added later share its scale."""
    mels = {}
    for sid in stem_ids:
        p = stems_dir / f"{sid}.flac"
        if p.exists():
            mels[sid] = _mel(p)
    if not mels:
        return existing
    ref = (existing or {}).get("ref")
    if not ref:
        # Loudest cells of everything together; children of a split stem are not double counted
        # because the caller passes only top-level stems for the first build.
        ref = float(np.percentile(sum(mels.values()), 99.5)) or 1.0
    out = dict(existing or {"version": VERSION, "cols": COLS, "bands": BANDS, "fmax": FMAX,
                            "rangeDb": RANGE_DB, "stems": {}})
    out["ref"] = ref
    out["stems"] = {**out.get("stems", {}), **{sid: _encode(S, ref) for sid, S in mels.items()}}
    return out


def for_song(stems_dir, stems):
    """Fresh spectrogram for a song's stem list (dicts from song.json)."""
    top = [s["id"] for s in stems if not s.get("parent")]
    data = build(stems_dir, top)
    children = [s["id"] for s in stems if s.get("parent")]
    if children:
        data = build(stems_dir, children, data)
    return data
