"""Render a custom mix (stem levels, pan, key, speed) to a file with ffmpeg + rubberband."""
import math
import subprocess
from pathlib import Path


def render(stems_dir: Path, mix: dict, key: float, tempo: float, out: Path):
    """mix: {stem_id: {"gain": 0..1.5, "pan": -1..1, "on": bool}}"""
    inputs, chains, labels = [], [], []
    for sid, m in mix.items():
        if not m.get("on", True) or m.get("gain", 1) <= 0.001:
            continue
        src = stems_dir / f"{sid}.flac"
        if not src.exists():
            continue
        g = float(m.get("gain", 1.0))
        p = max(-1.0, min(1.0, float(m.get("pan", 0.0))))
        # Equal-power pan applied as a per-channel gain.
        l, r = g * math.cos((p + 1) * math.pi / 4) * math.sqrt(2), g * math.sin((p + 1) * math.pi / 4) * math.sqrt(2)
        n = len(labels)
        inputs += ["-i", str(src)]
        chains.append(f"[{n}:a]pan=stereo|c0={l:.4f}*c0|c1={r:.4f}*c1[s{n}]")
        labels.append(f"[s{n}]")
    if not labels:
        raise ValueError("Nothing to export: every stem is muted")
    graph = ";".join(chains) + f";{''.join(labels)}amix=inputs={len(labels)}:normalize=0[m]"
    post = []
    if abs(key) > 0.01 or abs(tempo - 1) > 0.001:
        post.append(f"rubberband=pitch={2 ** (key / 12):.6f}:tempo={tempo:.4f}:formant=preserved")
    post.append("alimiter=limit=0.95")
    graph += f";[m]{','.join(post)}[o]"
    codec = ["-c:a", "libmp3lame", "-b:a", "320k"] if out.suffix == ".mp3" else ["-c:a", "pcm_s16le"]
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[o]", *codec, str(out)],
                   check=True)
    return out
