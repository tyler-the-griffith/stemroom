"""Audio regressions using synthetic stems; no models or live library writes."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
import soundfile as sf

from server.pipeline import harmonies


class HarmonyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stemroom-harmonies-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rate = 8000
        self.t = np.arange(self.rate * 10) / self.rate

    def voice(self, spans, frequency=220, level=0.2):
        gate = sum(((self.t >= a) & (self.t < b)) for a, b in spans)
        mono = level * np.sin(2 * np.pi * frequency * self.t) * gate
        return np.stack([mono, mono * 0.7], axis=1).astype(np.float32)

    def run_split(self, lead, backing):
        self.lead = self.root / "lead.wav"
        self.backing = self.root / "backing.wav"
        sf.write(self.lead, lead, self.rate, subtype="FLOAT")
        sf.write(self.backing, backing, self.rate, subtype="FLOAT")
        original = hashlib.sha256(self.backing.read_bytes()).digest()
        paths, report = harmonies.split(self.lead, self.backing, self.root / "out")
        self.assertEqual(hashlib.sha256(self.backing.read_bytes()).digest(), original)
        h, rate = sf.read(paths["backing_harmony"], dtype="float32", always_2d=True)
        o, _ = sf.read(paths["backing_other"], dtype="float32", always_2d=True)
        self.assertEqual(rate, self.rate)
        self.assertEqual(h.shape, backing.shape)
        np.testing.assert_allclose(h + o, backing, atol=2e-8, rtol=0)
        return h, o, report

    def energy(self, audio, start, end):
        return float(np.mean(audio[int(start * self.rate):int(end * self.rate)] ** 2))

    def test_overlap_moves_only_simultaneous_backing_and_preserves_stereo(self):
        lead = self.voice([(1, 3), (6, 8)])
        backing = self.voice([(2, 4), (5, 7)], frequency=330)
        h, o, report = self.run_split(lead, backing)
        self.assertGreater(self.energy(h, 2.2, 2.8), 0.01)
        self.assertLess(self.energy(o, 2.2, 2.8), 1e-12)
        self.assertGreater(self.energy(o, 3.5, 3.9), 0.01)
        self.assertGreater(self.energy(o, 5.2, 5.7), 0.01)
        self.assertLess(self.energy(h, 5.2, 5.7), 1e-12)
        self.assertGreater(self.energy(h, 6.2, 6.8), 0.01)
        self.assertTrue(report["regions"])
        self.assertAlmostEqual(self.energy(h[:, 1], 2.2, 2.8) / self.energy(h[:, 0], 2.2, 2.8), .49, places=5)

    def test_silent_lead_leaves_the_complete_backing_track_alone(self):
        backing = self.voice([(0, 10)])
        h, o, report = self.run_split(np.zeros_like(backing), backing)
        self.assertEqual(float(np.max(np.abs(h))), 0)
        np.testing.assert_array_equal(o, backing)
        self.assertEqual(report["overlapSeconds"], 0)

    def test_silent_backing_and_very_short_recordings_are_valid(self):
        zeros = np.zeros((80, 2), np.float32)
        h, o, report = self.run_split(zeros, zeros)
        self.assertEqual(h.shape, zeros.shape)
        self.assertEqual(report["regions"], [])

    def test_syllable_gaps_are_bridged_and_edges_are_crossfaded(self):
        lead = self.voice([(1, 2), (2.1, 3)])
        backing = np.full_like(lead, .2)
        h, _, _ = self.run_split(lead, backing)
        np.testing.assert_allclose(h[round(2.05 * self.rate)], [.2, .2])
        mask = h[:, 0] / .2
        self.assertLess(float(np.max(np.abs(np.diff(mask)))), .003)
        self.assertTrue(np.any((mask > 0) & (mask < 1)))

    def test_quiet_masters_work_but_near_silent_bleed_does_not_trigger(self):
        lead = self.voice([(1, 3)], level=.003)
        backing = self.voice([(0, 10)], level=.002)
        h, _, report = self.run_split(lead, backing)
        self.assertGreater(self.energy(h, 1.2, 2.8), 1e-6)
        self.assertTrue(report["regions"])
        h, _, report = self.run_split(lead * .001, backing)
        self.assertEqual(float(np.max(np.abs(h))), 0)
        self.assertEqual(report["regions"], [])

    def test_misaligned_inputs_are_rejected_before_outputs_are_written(self):
        a = self.voice([(1, 3)])
        sf.write(self.root / "lead.wav", a, self.rate)
        sf.write(self.root / "backing.wav", a, self.rate * 2)
        with self.assertRaisesRegex(ValueError, "matching"):
            harmonies.split(self.root / "lead.wav", self.root / "backing.wav", self.root / "out")
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
