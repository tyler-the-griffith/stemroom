"""Partition an existing backing-vocal stem by activity in the isolated lead.

This detects simultaneous vocals, not musical intervals or individual singers.
Complementary, smoothly crossfaded masks preserve the complete backing recording.
No model, GPU, normalization, or lyric alignment is needed.
"""
from pathlib import Path

import numpy as np
import soundfile as sf

VERSION = 1
CHILDREN = ("backing_harmony", "backing_other")
FRAME_SECONDS = 0.02


def _runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _envelope(path):
    with sf.SoundFile(path) as audio:
        rate, frames = audio.samplerate, len(audio)
        hop = max(1, round(rate * FRAME_SECONDS))
        envelope = []
        for block in audio.blocks(blocksize=hop * 1000, dtype="float32", always_2d=True):
            power = np.mean(block.astype(np.float64) ** 2, axis=1)
            n = int(np.ceil(len(power) / hop))
            padded = np.pad(power, (0, n * hop - len(power)))
            envelope.extend(np.sqrt(padded.reshape(n, hop).mean(axis=1)))
    return np.asarray(envelope), rate, frames, hop


def _activity(envelope, hop_seconds):
    db = 20 * np.log10(np.maximum(envelope, 1e-12))
    # Ignore digital silence when estimating the singer's own level. A fixed
    # threshold alone misses quiet masters; the floor rejects near-silent bleed.
    audible = db[db > -80]
    reference = float(np.percentile(audible, 95)) if len(audible) else -120.0
    on = max(-60.0, reference - 25)
    off = on - 6
    active = np.zeros(len(db), dtype=bool)
    singing = False
    for i, level in enumerate(db):
        singing = level >= (off if singing else on)
        active[i] = singing
    # Bridge short gaps between syllables, but retain separate backing answers.
    for start, end in _runs(~active):
        if start > 0 and end < len(active) and (end - start) * hop_seconds <= 0.18:
            active[start:end] = True
    for start, end in _runs(active):
        if (end - start) * hop_seconds < 0.10:
            active[start:end] = False
    return active, round(on, 2)


def split(lead: Path, backing: Path, out: Path):
    """Return two full-length float WAVs plus a report; never change the sources."""
    lead_rms, rate, lead_frames, hop = _envelope(lead)
    backing_rms, backing_rate, frames, backing_hop = _envelope(backing)
    if rate != backing_rate or hop != backing_hop or abs(lead_frames - frames) > rate * 0.1:
        raise ValueError("Lead and backing vocals must have matching sample rates and lengths")
    if not frames:
        raise ValueError("The backing vocal recording is empty")
    dt = hop / rate
    lead_active, lead_threshold = _activity(lead_rms, dt)
    backing_active, backing_threshold = _activity(backing_rms, dt)
    n = len(backing_rms)
    lead_active = np.pad(lead_active, (0, max(0, n - len(lead_active))))[:n]
    # Include consonants and short vocal tails around each lead phrase.
    expanded = np.zeros(n, dtype=bool)
    for start, end in _runs(lead_active):
        expanded[max(0, start - round(0.04 / dt)):min(n, end + round(0.12 / dt))] = True
    overlap = expanded & backing_active
    regions = [{"start": round(a * dt, 3), "end": round(min(b * dt, frames / rate), 3)}
               for a, b in _runs(overlap) if (b - a) * dt >= 0.10]
    # The mask follows the lead, even through low-energy backing syllables. Do
    # not repeatedly gate the harmony itself and create pumping between words.
    radius = max(1, round(0.04 / dt))
    kernel = np.hanning(2 * radius + 3)[1:-1]
    kernel /= kernel.sum()
    smooth = np.convolve(np.pad(expanded.astype(float), radius, mode="edge"), kernel, mode="valid")
    positions = (np.arange(n) + 0.5) * hop
    out.mkdir(parents=True, exist_ok=True)
    paths = {sid: out / f"{sid}.wav" for sid in CHILDREN}
    with sf.SoundFile(backing) as src, \
            sf.SoundFile(paths[CHILDREN[0]], "w", samplerate=rate, channels=src.channels, subtype="FLOAT") as harmony, \
            sf.SoundFile(paths[CHILDREN[1]], "w", samplerate=rate, channels=src.channels, subtype="FLOAT") as other:
        start = 0
        for block in src.blocks(blocksize=rate * 10, dtype="float32", always_2d=True):
            mask = np.interp(np.arange(start, start + len(block)), positions, smooth).astype(np.float32)
            overlapping = block * mask[:, None]
            harmony.write(overlapping)
            other.write(block - overlapping)
            start += len(block)
    return paths, {"version": VERSION, "method": "lead-vocal-activity", "regions": regions,
                   "overlapSeconds": round(sum(r["end"] - r["start"] for r in regions), 2),
                   "leadThresholdDb": lead_threshold, "backingThresholdDb": backing_threshold}
