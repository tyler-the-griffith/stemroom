import base64
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from server.pipeline import spectrogram


def tone(path, freq, seconds=6, sr=22050, level=0.3):
    t = np.arange(int(sr * seconds)) / sr
    sf.write(path, (level * np.sin(2 * np.pi * freq * t)).astype(np.float32), sr, format="FLAC")


def grid(data, sid):
    raw = np.frombuffer(base64.b64decode(data["stems"][sid]), dtype=np.uint8)
    return raw.reshape(data["bands"], data["cols"])


class SpectrogramTests(unittest.TestCase):
    def test_stems_share_a_grid_and_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            tone(d / "bass.flac", 80)
            tone(d / "lead.flac", 1200)
            tone(d / "snare.flac", 3000, level=0.1)
            stems = [{"id": "bass"}, {"id": "lead"}, {"id": "drums"}, {"id": "snare", "parent": "drums"}]
            data = spectrogram.for_song(d, stems)

            self.assertEqual(set(data["stems"]), {"bass", "lead", "snare"})  # no file, no layer
            bass, lead = grid(data, "bass"), grid(data, "lead")
            self.assertEqual(bass.shape, (spectrogram.BANDS, spectrogram.COLS))
            # Row 0 is the top (high pitch): the bass tone peaks lower in the grid than the lead.
            self.assertGreater(int(bass.mean(axis=1).argmax()), int(lead.mean(axis=1).argmax()))

            # A stem added later keeps the original loudness reference.
            tone(d / "kick.flac", 60)
            more = spectrogram.build(d, ["kick"], data)
            self.assertEqual(more["ref"], data["ref"])
            self.assertEqual(set(more["stems"]), {"bass", "lead", "snare", "kick"})
            self.assertEqual(more["stems"]["bass"], data["stems"]["bass"])


if __name__ == "__main__":
    unittest.main()
