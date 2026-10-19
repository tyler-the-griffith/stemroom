"""Paths and tunables shared by the web server and the worker process."""
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STEMROOM_DATA", ROOT / "data"))
SONGS = DATA / "songs"
MODELS = Path(os.environ.get("STEMROOM_MODELS", DATA / "models"))
EXPORTS = DATA / "exports"
WEB = ROOT / "web"

PORT = int(os.environ.get("STEMROOM_PORT", "8765"))
PUBLIC_URL = os.environ.get('STEMROOM_PUBLIC_URL', '').rstrip('/')
# Explicit frontend origins; the default local installation needs no CORS.
FRONTEND_ORIGINS = [v.strip() for v in os.environ.get('STEMROOM_FRONTEND_ORIGINS', '').split(',') if v.strip()]
if PUBLIC_URL:
    public = urlsplit(PUBLIC_URL)
    if (public.scheme != 'https' or not public.hostname or public.username or public.password
            or public.query or public.fragment or public.path not in ('', '/stemroom')):
        raise ValueError('STEMROOM_PUBLIC_URL must be an HTTPS origin, optionally ending in /stemroom')

# "auto" uses CUDA when torch can see it, "cpu" forces CPU (e.g. while the VM owns the GPU).
DEVICE = os.environ.get("STEMROOM_DEVICE", "auto")

PYTHON = sys.executable

# Separation models (names as listed by `audio-separator --list_models`).
MODEL_VOCALS = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
MODEL_KARAOKE = "mel_band_roformer_karaoke_aufr33_viperx_sdr_10.1956.ckpt"
MODEL_INSTRUMENTS = "htdemucs_6s.yaml"
MODEL_DRUMSEP = "MDX23C-DrumSep-aufr33-jarredou.ckpt"
MODEL_DEREVERB = "dereverb_mel_band_roformer_less_aggressive_anvuew_sdr_18.8050.ckpt"
WHISPER_MODEL = "large-v3-turbo"

# Stem ids -> display name and palette colour. Order is mixer order.
STEM_INFO = {
    "lead": ("Lead vocals", "#E09F3E"),
    "backing": ("Backing vocals", "#FFF3B0"),
    "backing_harmony": ("Overlapping harmonies", "#F0C27A"),
    "backing_other": ("Other backing vocals", "#FFF3B0"),
    "vocals": ("Vocals", "#E09F3E"),
    "drums": ("Drums", "#D4605A"),
    "bass": ("Bass", "#7FAFBA"),
    "guitar": ("Guitar", "#F0C27A"),
    "piano": ("Piano", "#B7D3D9"),
    "other": ("Everything else", "#A3AE9C"),
    "kick": ("Kick", "#D4605A"),
    "snare": ("Snare", "#E07A6E"),
    "toms": ("Toms", "#B8463F"),
    "hh": ("Hi-hat", "#EBA79C"),
    "ride": ("Ride", "#C9786F"),
    "crash": ("Crash", "#F2C3BA"),
    "lead_dry": ("Lead vocals (dry)", "#E09F3E"),
}
VOCAL_STEMS = {"lead", "vocals", "lead_dry"}


def cuda_env():
    """Environment for the worker so ctranslate2 (Whisper) finds torch's bundled CUDA libs."""
    env = dict(os.environ)
    site = Path(sys.prefix) / "lib"
    libs = []
    for p in site.glob("python3*/site-packages/nvidia/*/lib"):
        libs.append(str(p))
    if libs:
        env["LD_LIBRARY_PATH"] = ":".join(libs + [env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
    env["PYTHONUNBUFFERED"] = "1"
    return env
