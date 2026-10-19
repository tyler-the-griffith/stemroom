"""Stem separation with python-audio-separator (BS-RoFormer, Mel-RoFormer karaoke, Demucs).

Runs only inside the worker process: importing torch here would make the web server
hold the GPU open, which blocks libvirt from handing it to the Windows VM.
"""
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .. import config

MDXC = {"segment_size": 256, "override_model_segment_size": False, "batch_size": 4, "overlap": 2, "pitch_shift": 0}
DEMUCS = {"segment_size": "Default", "shifts": 1, "overlap": 0.25, "segments_enabled": True}


def _separator(out_dir: Path):
    import torch
    from audio_separator.separator import Separator

    cuda = torch.cuda.is_available()
    return Separator(
        output_dir=str(out_dir),
        model_file_dir=str(config.MODELS),
        output_format="WAV",
        use_autocast=cuda,
        mdxc_params=MDXC,
        demucs_params=DEMUCS,
        log_level=30,  # warnings only; tqdm still reports progress on stderr
    ), cuda


def _run(sep, model: str, src: Path, names: dict):
    sep.load_model(model_filename=model)
    files = sep.separate(str(src), names)
    by_label = {k.lower(): v for k, v in names.items()}
    out = {}
    for f in files:
        stem = Path(f).stem
        if stem not in names.values():
            # Custom name not applied: fall back to the "(Label)" in the default file name.
            label = stem[stem.rfind("(") + 1:stem.rfind(")")].lower() if "(" in stem else ""
            stem = by_label.get(label, stem)
        out[stem] = Path(sep.output_dir) / Path(f).name
    return out


def separate(mix: Path, work: Path, quality: str, stage) -> dict:
    """Returns {stem_id: wav path}. `stage(label, lo, hi)` announces progress ranges."""
    work.mkdir(parents=True, exist_ok=True)
    sep, cuda = _separator(work)
    instruments = {k: f"i_{k.lower()}" for k in ["Vocals", "Drums", "Bass", "Guitar", "Piano", "Other"]}
    out = {}

    if quality == "fast":
        stage("Separating instruments", 0.0, 1.0)
        r = _run(sep, config.MODEL_INSTRUMENTS, mix, instruments)
        out["lead"] = r["i_vocals"]
    else:
        stage("Isolating vocals", 0.0, 0.55)
        r1 = _run(sep, config.MODEL_VOCALS, mix, {"Vocals": "vocals", "Instrumental": "instrumental"})
        stage("Splitting lead from backing vocals", 0.55, 0.88)
        r2 = _run(sep, config.MODEL_KARAOKE, r1["vocals"], {"Vocals": "lead", "Instrumental": "backing"})
        out["lead"], out["backing"] = r2["lead"], r2["backing"]
        stage("Separating instruments", 0.88, 1.0)
        r = _run(sep, config.MODEL_INSTRUMENTS, r1["instrumental"], instruments)
        # Demucs' "vocals" on an instrumental is just leftover bleed: fold it into "other".
        _sum_into(r["i_other"], r["i_vocals"])

    for k in ["drums", "bass", "guitar", "piano", "other"]:
        out[k] = r[f"i_{k}"]
    return out


def refine(src: Path, op: str, work: Path, stage) -> dict:
    """Split one stem further. Returns {new_stem_id: wav path}."""
    work.mkdir(parents=True, exist_ok=True)
    sep, _ = _separator(work)
    if op == "drumsep":
        stage("Splitting drums into kit pieces", 0.0, 1.0)
        names = {k: k.lower() for k in ["Kick", "Snare", "Toms", "HH", "Ride", "Crash"]}
        r = _run(sep, config.MODEL_DRUMSEP, src, names)
        return {k: r[k] for k in ["kick", "snare", "toms", "hh", "ride", "crash"] if k in r}
    if op == "dereverb":
        stage("Removing room reverb", 0.0, 1.0)
        r = _run(sep, config.MODEL_DEREVERB, src, {"noreverb": "dry", "reverb": "wet"})
        return {"lead_dry": r["dry"]}
    raise ValueError(f"unknown refine op {op}")


def _sum_into(dst: Path, extra: Path):
    a, sr = sf.read(dst, dtype="float32", always_2d=True)
    b, _ = sf.read(extra, dtype="float32", always_2d=True)
    n = min(len(a), len(b))
    a[:n] += b[:n]
    sf.write(dst, a, sr, subtype="PCM_16")


def to_wav(src, dst: Path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", "44100", str(dst)], check=True)


def encode(wav: Path, stems_dir: Path, stem_id: str):
    """FLAC for downloads and Opus for streaming to the browser."""
    stems_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "flac", str(stems_dir / f"{stem_id}.flac")], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "160k",
                    str(stems_dir / f"{stem_id}.opus")], check=True)


def loudness_db(wav: Path) -> float:
    a, _ = sf.read(wav, dtype="float32", always_2d=True)
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)) + 1e-12)
    return 20 * np.log10(rms)


def peaks(wav: Path, points: int = 1600):
    a, _ = sf.read(wav, dtype="float32", always_2d=True)
    mono = np.abs(a).max(axis=1)
    n = len(mono) // points * points
    if n == 0:
        return [0] * points
    return mono[:n].reshape(points, -1).max(axis=1)


def cleanup(work: Path):
    shutil.rmtree(work, ignore_errors=True)
