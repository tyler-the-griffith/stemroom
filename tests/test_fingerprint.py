import math
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from server.pipeline import fingerprint

class FingerprintTests(unittest.TestCase):
    def test_reencoded_audio_and_different_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)
            for name,shift in [('original',0),('different',300)]:
                with wave.open(str(d/f'{name}.wav'),'wb') as w:
                    w.setparams((1,2,8000,0,'NONE','not compressed'))
                    samples=[]
                    for i in range(8000*30):
                        t=i/8000;freq=220+55*(int(t*2)%9)+shift
                        value=.5*math.sin(2*math.pi*freq*t)+.2*math.sin(2*math.pi*freq*2*t)
                        samples.append(struct.pack('<h',int(12000*value)))
                    w.writeframes(b''.join(samples))
            subprocess.run(['ffmpeg','-v','error','-i',str(d/'original.wav'),'-b:a','96k',str(d/'reencoded.mp3')],check=True)
            original=fingerprint.make(d/'original.wav')
            recoded=fingerprint.make(d/'reencoded.mp3')
            other=fingerprint.make(d/'different.wav')
            self.assertEqual(fingerprint.matches(recoded,{'same':original})[0][0],'same')
            self.assertEqual(fingerprint.matches(other,{'same':original}),[])
