"""Conservative local audio similarity suggestions, never automatic deduplication.

Compare time-aligned spectral features from three excerpts. Re-encodes normally match;
remixes, edits and different recordings may not. Exact hashes/YouTube IDs are authoritative.
"""
import json
import subprocess

def make(path):
    import numpy as np
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(path)],capture_output=True,check=True)
    duration=float(json.loads(result.stdout)['format']['duration'])
    rows=[]
    for frac in (0.15,0.45,0.7):
        start=max(0,min(duration-12,duration*frac))
        pcm=subprocess.run(['ffmpeg','-v','error','-ss',str(start),'-i',str(path),'-t','12','-vn','-ac','1','-ar','8000','-f','f32le','-'],capture_output=True,check=True).stdout
        samples=np.frombuffer(pcm,dtype='<f4')
        if len(samples)<4096: return None
        frames=np.lib.stride_tricks.sliding_window_view(samples,2048)[::1024]
        spectrum=np.abs(np.fft.rfft(frames*np.hanning(2048)))**2
        edges=np.geomspace(8,1024,33).astype(int)
        bands=np.stack([spectrum[:,a:b].mean(axis=1) for a,b in zip(edges[:-1],edges[1:])],axis=1)
        # Aggregate two seconds at a time to tolerate codec padding.
        for chunk in np.array_split(bands,6):
            values=np.log1p(chunk.mean(axis=0)*1000)
            values-=values.mean()
            values/=max(float(np.linalg.norm(values)),1e-8)
            rows.extend(values.round(4).tolist())
    return {'duration':duration,'vector':rows}

def matches(probe, existing):
    if not probe: return []
    import numpy as np
    out=[]
    a=np.array(probe['vector']).reshape(-1,32)
    for sid, value in existing.items():
        if abs(value['duration']-probe['duration'])>max(2,probe['duration']*.008): continue
        b=np.array(value['vector']).reshape(-1,32)
        if a.shape!=b.shape: continue
        score=float(np.mean(np.sum(a*b,axis=1)))
        if score>.985: out.append((sid,score))
    return sorted(out,key=lambda pair:-pair[1])
