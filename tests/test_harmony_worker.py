"""Publication, retries and source-version safety for the CPU harmony job."""
import hashlib
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from server import config, catalog, store, worker


class HarmonyWorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="stemroom-harmony-worker-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for key, value in {"DATA":self.root,"SONGS":self.root/"songs"}.items():
            p = patch.object(config,key,value);p.start();self.addCleanup(p.stop)
        catalog.initialize()
        self.sid = "harmony-test"
        self.song = {"id":self.sid,"title":"Keep my title","thumbnail":"/my-cover.png","status":"ready",
                     "stems":[{"id":"lead"},{"id":"backing"}],"assetVersion":"original"}
        store.write_song(self.sid,self.song)
        self.d = store.asset_dir(self.sid)
        self.make_audio(self.d)
        self.mix = catalog.save_mix(self.sid,"Custom",{"stems":{"lead":{"vol":.6},"backing":{"vol":.4}}},"original")
        catalog.document("room",{"current":{"mixSnapshot":self.mix["settings"],"assetVersion":"original"}})

    def make_audio(self, directory):
        (directory / "stems").mkdir(parents=True,exist_ok=True)
        t = np.arange(8000 * 8) / 8000
        for sid, bounds, frequency in [("lead",(1,3),220),("backing",(2,5),330)]:
            audio = .2 * np.sin(2*np.pi*frequency*t) * ((t>=bounds[0]) & (t<bounds[1]))
            sf.write(directory / "stems" / f"{sid}.flac",np.stack([audio,audio*.8],axis=1),8000)
        store._atomic_write(directory / "peaks.json",{"lead":[60]*1600,"backing":[50]*1600})

    def test_publication_preserves_sources_artwork_mixes_and_is_idempotent(self):
        original = (self.d/"stems/backing.flac").read_bytes()
        worker.task_harmonies(self.sid,{})
        song = store.read_song(self.sid)
        self.assertEqual(song["thumbnail"],self.song["thumbnail"])
        self.assertEqual(song["title"],self.song["title"])
        parent = next(s for s in song["stems"] if s["id"]=="backing")
        self.assertEqual(parent["replacedBy"],["backing_harmony","backing_other"])
        self.assertEqual((self.d/"stems/backing.flac").read_bytes(),original)
        self.assertEqual(catalog.get_mix(self.sid)["settings"],self.mix["settings"])
        self.assertEqual(catalog.document("room")["current"]["mixSnapshot"],self.mix["settings"])
        original_audio,_=sf.read(self.d/"stems/backing.flac")
        harmony,_=sf.read(self.d/"stems/backing_harmony.flac")
        other,_=sf.read(self.d/"stems/backing_other.flac")
        np.testing.assert_allclose(harmony+other,original_audio,atol=2.5e-7,rtol=0)
        for sid in parent["replacedBy"]:
            self.assertGreater((self.d/"stems"/f"{sid}.opus").stat().st_size,100)
            self.assertEqual(len(store.read_part(self.sid,"peaks")[sid]),1600)
        with patch.object(worker.sep,"encode",side_effect=AssertionError("must not rerun")):
            worker.task_harmonies(self.sid,{})

    def test_encoding_failure_never_publishes_partial_stems(self):
        before=(self.d/"song.json").read_bytes()
        encode=worker.sep.encode
        def fail_second(wav,dest,sid):
            if sid=="backing_other":raise RuntimeError("disk full")
            encode(wav,dest,sid)
        with patch.object(worker.sep,"encode",side_effect=fail_second),self.assertRaisesRegex(RuntimeError,"disk full"):
            worker.task_harmonies(self.sid,{})
        self.assertEqual((self.d/"song.json").read_bytes(),before)
        self.assertFalse((self.d/"stems/backing_harmony.flac").exists())
        worker.task_harmonies(self.sid,{})
        self.assertIn("harmonyAnalysis",store.read_song(self.sid))

    def test_refining_replacement_does_not_change_an_older_queued_source(self):
        original=hashlib.sha256((self.d/"stems/backing.flac").read_bytes()).digest()
        store.update_song(self.sid,assetVersion="newversion")
        replacement=store.asset_dir(self.sid)
        self.make_audio(replacement)
        worker.task_refine(self.sid,{"stem":"backing","op":"harmonies"})
        self.assertTrue((replacement/"stems/backing_harmony.flac").exists())
        self.assertFalse((self.d/"stems/backing_harmony.flac").exists())
        self.assertEqual(hashlib.sha256((self.d/"stems/backing.flac").read_bytes()).digest(),original)
        self.assertNotIn("harmonyAnalysis",store.read_song(self.sid,"original"))
        self.assertEqual(catalog.document("room")["current"]["assetVersion"],"original")

    def test_new_best_quality_imports_split_harmonies_automatically(self):
        store.update_song(self.sid,source={"type":"upload"},status="processing")
        samples,rate=sf.read(self.d/"stems/backing.flac")
        sf.write(self.d/"source.wav",samples,rate)
        def separate(mix,work,quality,progress):
            work.mkdir(parents=True,exist_ok=True)
            result={}
            for sid in ["lead","backing","drums","bass","guitar","piano","other"]:
                result[sid]=work/f"{sid}.flac"
                shutil.copy2(self.d/"stems"/("lead.flac" if sid=="lead" else "backing.flac"),result[sid])
            return result
        with patch.object(worker.sep,"separate",side_effect=separate), \
             patch('server.pipeline.analysis.analyze',return_value={"key":None,"tempo":100,"beats":[]}), \
             patch.object(worker,"build_lyrics"):
            worker.task_process(self.sid,{"quality":"best"})
        song=store.read_song(self.sid)
        self.assertEqual(song["status"],"ready")
        self.assertEqual(song["harmonyAnalysis"]["version"],1)
        ids=[s["id"] for s in song["stems"]]
        self.assertEqual(ids[:4],["lead","backing","backing_harmony","backing_other"])
        self.assertEqual(song["thumbnail"],self.song["thumbnail"])


if __name__=="__main__":unittest.main()
