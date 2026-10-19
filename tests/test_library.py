"""Integration regression tests. Everything is isolated in a temporary data directory."""
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

TEMP=tempfile.TemporaryDirectory(prefix='stemroom-tests-')
os.environ['STEMROOM_DATA']=TEMP.name
os.environ['STEMROOM_FRONTEND_ORIGINS']='https://tylergriffith.us'
root=Path(TEMP.name)
sid='yt-abcdefghijk'
songdir=root/'songs'/sid
songdir.mkdir(parents=True)
cover='https://example.test/preserved-cover.jpg'
(songdir/'song.json').write_text(json.dumps({'id':sid,'title':'Song','artist':'Artist','thumbnail':cover,'status':'ready','created':1,'duration':20,'source':{'type':'youtube','videoId':'abcdefghijk'},'stems':[{'id':'lead'}]}))
(songdir/'mix.json').write_text(json.dumps({'stems':{'lead':{'vol':.7}},'master':.8}))
(songdir/'lyrics.json').write_text(json.dumps({'lines':[{'start':0,'end':5,'text':'Hello','singer':1}]}))
(songdir/'stems').mkdir()
(songdir/'stems'/'lead.opus').write_bytes(b'original audio')
(root/'room.json').write_text(json.dumps({'queue':[],'current':{'id':'legacy','songId':sid,'singers':['A'],'key':-2,'tempo':.9,'guide':.3,'backing':True}}))

from fastapi.testclient import TestClient
from server import config
config.DATA=root;config.SONGS=root/"songs";config.EXPORTS=root/"exports";config.MODELS=root/"models"
config.FRONTEND_ORIGINS=['https://tylergriffith.us']
from server import app as server, catalog, store, library

class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.submissions=[]
        def submit(task,song,args=None):
            cls.submissions.append((task,song,args))
            fut=asyncio.get_running_loop().create_future();fut.set_result({});return fut
        cls.patcher=patch.object(server.jobs,'submit',side_effect=submit);cls.patcher.start()
        cls.client=TestClient(server.app)
        cls.client.__enter__()
    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None);cls.patcher.stop()
    def test_join_link_uses_public_origin_for_https_visitors(self):
        with patch.object(server, 'lan_ip', return_value='192.168.1.5'):
            local = self.client.get('/api/info').json()
            self.assertEqual(local['joinUrl'], f'http://192.168.1.5:{config.PORT}/#/join')
            public = TestClient(server.app, base_url='https://room.example')
            try:
                info = public.get('/api/info').json()
                self.assertEqual(info['joinUrl'], 'https://room.example/#/join')
                self.assertEqual(info['lanUrl'], f'http://192.168.1.5:{config.PORT}')
            finally:
                public.close()

    def test_subpath_serves_assets_api_audio_and_websocket(self):
        redirect = self.client.get('/stemroom?from=site', follow_redirects=False)
        self.assertEqual(redirect.status_code, 307)
        self.assertEqual(redirect.headers['location'], '/stemroom/?from=site')
        for path in ['/', '/js/main.js', '/js/urls.mjs', '/vendor/SignalsmithStretch.mjs', '/css/app.css', '/api/state']:
            self.assertEqual(self.client.get('/stemroom'+path).status_code, 200, path)
        audio = self.client.get(f'/stemroom/media/{sid}/lead.opus', headers={'Range':'bytes=0-3'})
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(len(audio.content), 4)
        with self.client.websocket_connect('/stemroom/ws?clientId=subpath-check') as ws:
            self.assertEqual(ws.receive_json()['type'], 'state')
        public = TestClient(server.app, base_url='https://site.test')
        try:
            with patch.object(config, 'PUBLIC_URL', ''):
                self.assertEqual(public.get('/stemroom/api/info').json()['joinUrl'], 'https://site.test/stemroom/#/join')
            with patch.object(config, 'PUBLIC_URL', 'https://tylergriffith.us/stemroom'):
                self.assertEqual(public.get('/stemroom/api/info').json()['joinUrl'], 'https://tylergriffith.us/stemroom/#/join')
        finally:
            public.close()

    def test_hosted_frontend_cors_for_controls_and_audio(self):
        origin = {'Origin': 'https://tylergriffith.us'}
        for method in ('POST', 'PATCH', 'PUT', 'DELETE'):
            response = self.client.options('/api/settings', headers={**origin,
                'Access-Control-Request-Method': method, 'Access-Control-Request-Headers': 'content-type'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['access-control-allow-origin'], origin['Origin'])
        audio = self.client.get(f'/media/{sid}/lead.opus', headers={**origin, 'Range': 'bytes=0-3'})
        self.assertEqual(audio.status_code, 206)
        self.assertEqual(audio.headers['access-control-allow-origin'], origin['Origin'])
        self.assertIn('Content-Range', audio.headers['access-control-expose-headers'])
        unknown = self.client.options('/api/settings', headers={'Origin': 'https://other.test',
            'Access-Control-Request-Method': 'PUT'})
        self.assertEqual(unknown.status_code, 400)
        self.assertNotIn('access-control-allow-origin', unknown.headers)

    def test_01_migration_keeps_artwork_and_queue(self):
        song=self.client.get(f'/api/songs/{sid}').json()
        self.assertEqual(song['song']['thumbnail'],cover)
        self.assertEqual(song['mixes'][0]['name'],'Default')
        self.assertEqual(song['mixes'][0]['settings']['stems']['lead']['vol'],.7)
        self.assertEqual(song['mixes'][0]['settings']['duet'],[1])
        current=self.client.get('/api/state').json()['current']
        self.assertEqual(current['key'],-2)
        self.assertEqual(current['assetVersion'],'original')
        self.assertEqual(current['mixSnapshot']['master'],.8)
        store.Room() # Idempotent migration
        self.assertEqual(len(catalog.mixes(sid)),1)
    def test_02_memberships_and_queue_snapshots(self):
        c1=self.client.post('/api/collections',json={'name':'Duets'}).json()
        c2=self.client.post('/api/collections',json={'name':'Party'}).json()
        mix=self.client.post(f'/api/songs/{sid}/mixes',json={'name':'Practice','settings':{'master':.4,'performance':{'key':3,'guide':.6},'duet':['all']}}).json()
        for c in [c1,c2]: self.assertEqual(self.client.put(f"/api/collections/{c['id']}/songs/{sid}",json={'mixId':mix['id']}).status_code,200)
        entry=self.client.post(f'/api/library/{sid}/queue',json={'mixId':mix['id'],'singers':'Sam & Lee'}).json()
        self.assertEqual(entry['key'],3);self.assertEqual(entry['mixSnapshot']['master'],.4)
        self.client.put(f"/api/songs/{sid}/mixes/{mix['id']}",json={'name':'Practice','settings':{'master':.9}})
        fresh=next(e for e in self.client.get('/api/state').json()['queue'] if e['id']==entry['id'])
        self.assertEqual(fresh['mixSnapshot']['master'],.4)
        self.client.delete(f"/api/collections/{c1['id']}")
        self.assertEqual(len(self.client.get(f"/api/collections/{c2['id']}/songs").json()),1)
        self.assertEqual(self.client.get(f'/api/songs/{sid}').json()['song']['thumbnail'],cover)
        self.assertEqual(self.client.put(f"/api/collections/{c2['id']}/order",json={'ids':[sid,sid]}).status_code,400)
    def test_03_duplicate_youtube_requires_choice(self):
        before=len(self.client.get('/api/state').json()['queue'])
        result=self.client.post('/api/queue',json={'url':'https://youtu.be/abcdefghijk','singers':'Lee'}).json()
        self.assertTrue(result['duplicate']);self.assertEqual(result['song']['thumbnail'],cover)
        self.assertEqual(len(self.client.get('/api/state').json()['queue']),before)
        r=self.client.post(f"/api/imports/{result['token']}/resolve",json={'action':'reuse'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(self.client.get('/api/state').json()['queue']),before+1)
        self.assertEqual(self.client.post(f"/api/imports/{result['token']}/resolve",json={'action':'reuse'}).status_code,404)
    def test_04_full_hash_duplicate_and_cancel(self):
        audio=b'test exact bytes'
        a=self.client.post('/api/upload',files={'file':('first.wav',audio,'audio/wav')}).json()
        b=self.client.post('/api/upload',files={'file':('different-name.wav',audio,'audio/wav')}).json()
        self.assertTrue(b['duplicate']);self.assertTrue(b['exact']);self.assertEqual(b['song']['id'],a['songId'])
        self.assertEqual(len(store.read_song(a['songId'])['source']['sha256']),64)
        self.client.post(f"/api/imports/{b['token']}/resolve",json={'action':'cancel'})
        self.assertFalse(library.token_dir(b['token']).exists())
    def test_05_safe_replacement_versions_and_mix_review(self):
        old=store.read_song(sid)
        stage=root/'replacements'/'testrevision'
        target=stage/'songs'/sid;target.mkdir(parents=True)
        store._atomic_write(target/'song.json',{**old,'status':'processing','thumbnail':None})
        with self.assertRaises(RuntimeError):library.promote(sid,{'staging':str(stage),'revision':'testrevision'})
        self.assertEqual(store.read_song(sid),old)
        store._atomic_write(target/'song.json',{**old,'status':'ready','duration':21,'thumbnail':None})
        (target/'stems').mkdir();(target/'stems'/'lead.opus').write_bytes(b'replacement audio')
        library.promote(sid,{'staging':str(stage),'revision':'testrevision'})
        self.assertEqual(store.read_song(sid)['thumbnail'],cover)
        self.assertEqual(self.client.get(f'/media/{sid}/lead.opus?v=original').content,b'original audio')
        self.assertEqual(self.client.get(f'/media/{sid}/lead.opus?v=testrevision').content,b'replacement audio')
        self.assertEqual(self.client.get(f'/api/songs/{sid}?version=original').json()['song']['duration'],20)
        self.assertTrue(all(m['needsReview'] for m in catalog.mixes(sid)))
        self.assertEqual(self.client.get('/api/state').json()['current']['assetVersion'],'original')
    def test_06_single_playback_owner(self):
        with self.client.websocket_connect('/ws?clientId=one') as a:
            a.receive_json()
            self.assertEqual(self.client.post('/api/player/claim',json={'clientId':'one'}).status_code,200)
            a.receive_json()
            with self.client.websocket_connect('/ws?clientId=two') as b:
                b.receive_json()
                self.assertEqual(self.client.post('/api/player/claim',json={'clientId':'two'}).status_code,409)
                self.assertEqual(self.client.post('/api/player/claim',json={'clientId':'two','takeover':True}).status_code,200)
                a.receive_json();b.receive_json()
                a.send_json({'type':'ended','entryId':'legacy'})
                # A following request verifies the old owner cannot advance the room.
                self.assertEqual(self.client.get('/api/state').json()['current']['id'],'legacy')
            self.assertIsNone(self.client.get('/api/state').json()['playerId'])
    def test_07_invalid_cross_song_mix(self):
        other=next(s['id'] for s in store.list_songs() if s['id']!=sid)
        mix=catalog.get_mix(other)
        self.assertEqual(self.client.post(f'/api/library/{sid}/queue',json={'mixId':mix['id']}).status_code,400)

    def test_08_concurrent_upload_has_one_library_song(self):
        from concurrent.futures import ThreadPoolExecutor
        def upload(_): return self.client.post('/api/upload',files={'file':('race.wav',b'concurrent new audio','audio/wav')}).json()
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(upload,range(2)))
        self.assertEqual(sum(bool(r.get('duplicate')) for r in results),1)
        unique=next(r for r in results if not r.get('duplicate'))
        duplicate=next(r for r in results if r.get('duplicate'))
        self.assertEqual(unique['songId'],duplicate['song']['id'])
        self.client.post(f"/api/imports/{duplicate['token']}/resolve",json={'action':'cancel'})

    def test_09_failed_worker_keeps_original(self):
        from server.jobs import Jobs
        async def check():
            async def changed(*_): pass
            runner=Jobs(changed)
            class Proc:
                def __init__(self):
                    self.stdout=asyncio.StreamReader();self.stdout.feed_data(b'@@{"event":"error","message":"test failure"}\n');self.stdout.feed_eof()
                    self.stderr=asyncio.StreamReader();self.stderr.feed_eof()
                async def wait(self):return 1
            async def create(*_,**__):return Proc()
            before=store.read_song(sid)
            with patch('asyncio.create_subprocess_exec',side_effect=create):
                with self.assertRaises(RuntimeError):await runner._run('process',sid,{'staging':str(root/'failed')})
            after=store.read_song(sid)
            self.assertEqual(after['status'],'ready');self.assertEqual(after['assetVersion'],before['assetVersion'])
            self.assertEqual(after['thumbnail'],cover);self.assertEqual(after['replacementError'],'test failure')
            self.assertNotIn(sid,runner.status)
        asyncio.run(check())

    def test_10_mix_validation(self):
        for value in [-1,20,'loud']:
            r=self.client.post(f'/api/songs/{sid}/mixes',json={'name':'Invalid','settings':{'master':value}})
            self.assertEqual(r.status_code,400)

    def test_11_harmony_refinement_validation_and_link_persistence(self):
        hsid='harmony-api'
        store.write_song(hsid,{'id':hsid,'title':'Harmony test','status':'ready','stems':[{'id':'lead'},{'id':'backing'}]})
        path=f'/api/songs/{hsid}/refine'
        self.assertEqual(self.client.post(path,json={'stem':'missing','op':'harmonies'}).status_code,400)
        self.assertEqual(self.client.post(path,json={'stem':'lead','op':'harmonies'}).status_code,400)
        self.assertEqual(self.client.post(path,json={'stem':'backing','op':'harmonies'}).status_code,200)
        self.assertIn(('refine',hsid,{'stem':'backing','op':'harmonies'}),self.submissions)
        store.update_song(hsid,harmonyAnalysis={'version':1})
        before=len(self.submissions)
        self.assertTrue(self.client.post(path,json={'stem':'backing','op':'harmonies'}).json()['alreadySeparated'])
        self.assertEqual(len(self.submissions),before)
        settings={'stems':{'backing_harmony':{'followLead':False,'vol':.4}}}
        mix=self.client.post(f'/api/songs/{hsid}/mixes',json={'name':'Independent harmonies','settings':settings})
        self.assertEqual(mix.status_code,200)
        self.assertEqual(mix.json()['settings'],settings)
        settings['stems']['backing_harmony']['followLead']='false'
        self.assertEqual(self.client.post(f'/api/songs/{hsid}/mixes',json={'name':'Invalid link','settings':settings}).status_code,400)

if __name__=='__main__': unittest.main()
