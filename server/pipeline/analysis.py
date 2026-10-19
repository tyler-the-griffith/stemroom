"""Tempo, beat grid, key and chord estimation with librosa."""
import numpy as np
import librosa

NOTES = ["C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B"]
# Krumhansl-Kessler key profiles.
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
SR = 22050
HOP = 512


def _load(paths):
    """Mono sum of the given stem files."""
    y = None
    for p in paths:
        a, _ = librosa.load(str(p), sr=SR, mono=True)
        y = a if y is None else y[: len(a)] + a[: len(y)]
    return y


def _chord_templates():
    names, temps = [], []
    for root in range(12):
        for quality, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
            t = np.zeros(12)
            for i in intervals:
                t[(root + i) % 12] = 1
            names.append(NOTES[root] + quality)
            temps.append(t / np.linalg.norm(t))
    return names, np.array(temps)


def analyze(rhythm_paths, harmony_paths):
    """rhythm: drums (or the mix); harmony: everything except vocals and drums."""
    y_r = _load(rhythm_paths)
    tempo, beat_frames = librosa.beat.beat_track(y=y_r, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=SR, hop_length=HOP)

    y_h = _load(harmony_paths)
    chroma = librosa.feature.chroma_cqt(y=y_h, sr=SR, hop_length=HOP)
    energy = librosa.feature.rms(y=y_h, hop_length=HOP)[0]

    # Key from the whole-song chroma profile.
    profile = chroma.mean(axis=1)
    best = (-2, 0, "major")
    for i in range(12):
        for mode, ref in (("major", MAJOR), ("minor", MINOR)):
            c = np.corrcoef(profile, np.roll(ref, i))[0, 1]
            if c > best[0]:
                best = (c, i, mode)
    key = {"tonic": best[1], "mode": best[2], "name": f"{NOTES[best[1]]} {best[2]}"}

    # Chords: beat-synchronous chroma matched against major/minor triads.
    names, temps = _chord_templates()
    frames = np.concatenate([[0], beat_frames, [chroma.shape[1]]]).astype(int)
    frames = np.unique(np.clip(frames, 0, chroma.shape[1]))
    sync = librosa.util.sync(chroma, frames, aggregate=np.median)
    e_sync = librosa.util.sync(energy[None, :], frames, aggregate=np.mean)[0]
    quiet = e_sync < (np.percentile(energy, 90) * 0.08)
    norm = sync / (np.linalg.norm(sync, axis=0, keepdims=True) + 1e-9)
    labels = []
    for j, idx in enumerate(np.argmax(temps @ norm, axis=0)):
        labels.append("N" if quiet[j] else names[idx])
    # Majority smoothing over a 3-beat window removes one-beat flickers.
    smooth = labels[:]
    for j in range(1, len(labels) - 1):
        if labels[j - 1] == labels[j + 1] != labels[j]:
            smooth[j] = labels[j - 1]
    times = librosa.frames_to_time(frames, sr=SR, hop_length=HOP)
    chords = []
    for j, lab in enumerate(smooth):
        start, end = float(times[j]), float(times[j + 1]) if j + 1 < len(times) else float(times[-1])
        if chords and chords[-1]["name"] == lab:
            chords[-1]["end"] = end
        else:
            chords.append({"name": lab, "start": start, "end": end})
    chords = [c for c in chords if c["name"] != "N" and c["end"] - c["start"] > 0.25]

    return {
        "tempo": round(tempo, 1),
        "beats": [round(float(b), 3) for b in beats],
        "key": key,
        "chords": [{**c, "start": round(c["start"], 3), "end": round(c["end"], 3)} for c in chords],
    }


def fallback_sections(duration, beats):
    """Without lyrics: evenly spaced 8-bar parts so the timeline still has landmarks."""
    if len(beats) < 64:
        return [{"label": "Song", "start": 0.0, "end": duration}]
    step = 32
    marks = [0.0] + [beats[i] for i in range(step, len(beats), step)] + [duration]
    return [{"label": f"Part {i + 1}", "start": round(marks[i], 2), "end": round(marks[i + 1], 2)}
            for i in range(len(marks) - 1)]
