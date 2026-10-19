"""Library, named mixes, collections, and non-destructive source imports."""
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool
from . import config, catalog, store
from .pipeline import fingerprint, ytdl

router=APIRouter()
lock=asyncio.Lock()
index_task=None
room=jobs=push=broadcast=enqueue=None

def configure(room_,jobs_,push_,broadcast_,enqueue_):
    global room,jobs,push,broadcast,enqueue
    room,jobs,push,broadcast,enqueue=room_,jobs_,push_,broadcast_,enqueue_

def require_song(sid):
    song=store.read_song(sid)
    if not song: raise HTTPException(404,'Song not found')
    return song

def ensure_mix(sid):
    if not catalog.mixes(sid):
        catalog.save_mix(sid,'Default',{'performance':{'key':0,'tempo':1,'guide':room.settings['defaultGuide'],'backing':True}},
                         require_song(sid).get('assetVersion','original'))

def describe(sid):
    ensure_mix(sid)
    s=store.song_summary(require_song(sid))
    s['mixes']=catalog.mixes(sid)
    s['defaultMixId']=catalog.get_mix(sid)['id']
    s['collections']=[c for c in catalog.collections() if any(m['songId']==sid for m in catalog.members(c['id']))]
    return s

@router.get('/api/library')
async def library():
    return {'songs':[describe(s['id']) for s in store.list_songs()], 'collections':catalog.collections()}

@router.post('/api/collections')
async def create_collection(body:dict):
    result=catalog.collection_save(body.get('name'))
    await changed(); return result

@router.patch('/api/collections/{cid}')
async def rename_collection(cid:str,body:dict):
    result=catalog.collection_save(body.get('name'),cid)
    await changed(); return result

@router.delete('/api/collections/{cid}')
async def delete_collection(cid:str):
    catalog.collection_delete(cid)
    await changed(); return {'ok':True}

@router.put('/api/collection-order')
async def collection_order(body:dict):
    catalog.order(None,body.get('ids',[]))
    await changed(); return {'ok':True}

@router.get('/api/collections/{cid}/songs')
async def collection_members(cid:str): return catalog.members(cid)

@router.put('/api/collections/{cid}/songs/{sid}')
async def add_member(cid:str,sid:str,body:dict):
    require_song(sid)
    catalog.member(cid,sid,body.get('mixId'))
    await changed(); return {'ok':True}

@router.delete('/api/collections/{cid}/songs/{sid}')
async def delete_member(cid:str,sid:str):
    catalog.member(cid,sid,remove=True)
    await changed(); return {'ok':True}

@router.put('/api/collections/{cid}/order')
async def member_order(cid:str,body:dict):
    catalog.order(cid,body.get('ids',[]))
    await changed(); return {'ok':True}

@router.post('/api/songs/{sid}/mixes')
async def create_mix(sid:str,body:dict):
    song=require_song(sid)
    result=catalog.save_mix(sid,body.get('name'),body.get('settings',{}),song.get('assetVersion','original'))
    await changed(); return result

@router.put('/api/songs/{sid}/mixes/{mid}')
async def update_mix(sid:str,mid:str,body:dict):
    song=require_song(sid)
    result=catalog.save_mix(sid,body.get('name'),body.get('settings',{}),song.get('assetVersion','original'),mid)
    await changed(); return result

@router.post('/api/songs/{sid}/mixes/{mid}/default')
async def make_default(sid:str,mid:str):
    catalog.default_mix(sid,mid)
    await changed(); return {'ok':True}

@router.post('/api/queue/{eid}/mix')
async def apply_mix(eid:str,body:dict):
    entry=room.find(eid)
    if not entry: raise HTTPException(404,'Performance not found')
    if entry.get('assetVersion','original')!=require_song(entry['songId']).get('assetVersion','original'):
        raise HTTPException(409,'This performance uses an older source. Queue the current version to use its mixes.')
    mix=catalog.get_mix(entry['songId'],body.get('mixId'))
    if not mix: raise HTTPException(404,'Mix not found')
    entry.update(mixId=mix['id'],mixName=mix['name'],mixSnapshot=mix['settings'])
    entry.update({k:v for k,v in mix['settings'].get('performance',{}).items() if k in ('key','tempo','guide','backing')})
    room.save(); await push(); return entry

async def changed():
    await broadcast({'type':'library'})
    await push()

def token_dir(token):
    if not re.fullmatch('[a-f0-9]{32}',token): raise ValueError('Invalid import')
    return config.DATA / 'imports' / token

def check_fingerprint(path):
    try: return fingerprint.make(path)
    except Exception: return None  # A decoder failure is reported by the processing job.

def extract_art(path,d):
    out=d/'artwork.jpg'
    try:
        subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:v:0','-frames:v','1','-vf','scale=800:-1','-y',str(out)],
                       capture_output=True,timeout=20,check=True)
        return out.is_file() and out.stat().st_size>0
    except Exception:
        out.unlink(missing_ok=True)
        return False

async def index_existing():
    """Backfill identity data without touching prepared stems or artwork."""
    known=catalog.fingerprint()
    for song in store.list_songs():
        if song['id'] in known: continue
        src=next(store.asset_dir(song['id']).glob('source.*'),None)
        if src:
            fp=await run_in_threadpool(check_fingerprint,src)
            if fp: catalog.fingerprint(song['id'],fp)
            def hash_file():
                with src.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
            digest=await run_in_threadpool(hash_file)
            catalog.identity('sha256',digest,song['id'])

async def youtube(body):
    vid=ytdl.video_id(body.get('url') or body.get('videoId') or '')
    if not vid: raise HTTPException(400,"That doesn't look like a YouTube link")
    token=uuid.uuid4().hex
    data={'token':token,'body':body,'created':time.time(),'source':{'type':'youtube','videoId':vid,'url':f'https://www.youtube.com/watch?v={vid}'},
          'title':body.get('title') or 'Fetching song info…','artist':body.get('channel') or '',
          'thumbnail':f'https://i.ytimg.com/vi/{vid}/hqdefault.jpg'}
    existing=catalog.identify('youtube',vid)
    if existing:
        data.update(match=existing,reason='Same YouTube video',exact=True)
        return await consider(data)
    d=token_dir(token); d.mkdir(parents=True,exist_ok=True)
    def meta(info):
        artist,title=ytdl.guess_artist_title(info)
        data.update(title=title,artist=artist,thumbnail=info.get('thumbnail') or data['thumbnail'])
    try:
        await run_in_threadpool(ytdl.download,vid,d,lambda _:None,meta)
    except Exception as e:
        shutil.rmtree(d,ignore_errors=True)
        raise HTTPException(502,f'Could not download this song: {e}')
    src=next(d.glob('source.*'))
    data['fingerprint']=await run_in_threadpool(check_fingerprint,src)
    return await consider(data)

async def upload(file,body):
    token=uuid.uuid4().hex; d=token_dir(token); d.mkdir(parents=True,exist_ok=True)
    ext=Path(file.filename or 'audio').suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}',ext): ext='.bin'
    src=d/f'source{ext}'; h=hashlib.sha256()
    with src.open('wb') as out:
        while chunk:=await file.read(1<<20): h.update(chunk);out.write(chunk)
    stem=Path(file.filename or 'Uploaded song').stem
    parts=re.match(r'^(?:\d+\s*[-.]\s*)?(.+?)\s+-\s+(.+)$',stem)
    data={'token':token,'body':body,'created':time.time(),'source':{'type':'file','name':file.filename,'sha256':h.hexdigest()},
          'title':body.get('title') or (parts[2] if parts else stem),'artist':body.get('artist') or (parts[1] if parts else ''),'thumbnail':None}
    data['match']=catalog.identify('sha256',h.hexdigest())
    if data['match']: data.update(reason='Identical audio file',exact=True)
    else: data['fingerprint']=await run_in_threadpool(check_fingerprint,src)
    if await run_in_threadpool(extract_art,src,d): data['embeddedArt']=True
    return await consider(data)

async def consider(data):
    if index_task and not index_task.done() and not data.get('exact'):
        await asyncio.shield(index_task)
    async with lock:
        # Recheck after concurrent downloads/uploads finish.
        source=data['source']
        exact=catalog.identify('youtube',source['videoId']) if source.get('videoId') else catalog.identify('sha256',source['sha256'])
        if exact: data.update(match=exact,reason='Same source already in your library',exact=True)
        if not data.get('match'):
            matches=fingerprint.matches(data.get('fingerprint'),catalog.fingerprint())
            if matches: data.update(match=matches[0][0],reason='Audio sounds like an existing recording',exact=False)
        if data.get('match'):
            catalog.pending(data['token'],data)
            return {'duplicate':True,'token':data['token'],'song':describe(data['match']),'reason':data['reason'],'exact':data.get('exact',False)}
        return await commit_import(data)

async def commit_import(data,replace=None):
    token=data['token']; d=token_dir(token); d.mkdir(parents=True,exist_ok=True)
    source=data['source']
    sid=replace or (f"yt-{source['videoId']}" if source.get('videoId') else f"f-{source['sha256'][:20]}")
    if not replace and store.read_song(sid): sid=f's-{uuid.uuid4().hex[:20]}'
    if replace and sid in jobs.status: raise HTTPException(409,'Wait for this song’s current job to finish')
    old=require_song(sid) if replace else None
    metadata={'id':sid,'status':'processing','created':old['created'] if old else time.time(),
              'title':old.get('title') if old else data['title'],'artist':old.get('artist') if old else data['artist'],
              'thumbnail':old.get('thumbnail') if old else data.get('thumbnail'),'source':source}
    if replace:
        staging=config.DATA/'replacements'/token
        target=staging/'songs'/sid;target.mkdir(parents=True,exist_ok=True)
        for f in d.iterdir(): shutil.move(str(f),target/f.name)
        store._atomic_write(target/'song.json',metadata)
        args={'quality':room.settings['quality'],'staging':str(staging),'revision':token,'fingerprint':data.get('fingerprint')}
        catalog.replacement(sid,args)
    else:
        target=store.song_dir(sid);target.mkdir(parents=True,exist_ok=True)
        for f in d.iterdir(): shutil.move(str(f),target/f.name)
        if data.get('embeddedArt'): metadata['thumbnail']=f'/artwork/{sid}'
        store.write_song(sid,metadata);ensure_mix(sid)
        if data.get('fingerprint'): catalog.fingerprint(sid,data['fingerprint'])
        args={'quality':room.settings['quality']}
    shutil.rmtree(d,ignore_errors=True)
    catalog.pending(token,delete=True)
    fut=jobs.submit('process',sid,args)
    fut.add_done_callback(lambda f:f.exception() if not f.cancelled() else None)
    # Replacement becomes available for new performances after it succeeds.
    entry=enqueue(sid,data['body']) if not replace else None
    if data['body'].get('collectionId') and not replace:
        catalog.member(data['body']['collectionId'],sid)
    await changed()
    return {'entry':entry,'replacement':bool(replace),'songId':sid}

@router.post('/api/imports/{token}/resolve')
async def resolve_import(token:str,body:dict):
    async with lock:
        data=catalog.pending(token)
        if not data: raise HTTPException(404,'This import was already resolved. Add the song again if needed.')
        action=body.get('action')
        if action=='cancel':
            shutil.rmtree(token_dir(token),ignore_errors=True);catalog.pending(token,delete=True);return {'cancelled':True}
        if action=='reuse':
            sid=data['match'];require_song(sid)
            src=data['source']
            for kind,key in [('youtube','videoId'),('sha256','sha256')]:
                if src.get(key): catalog.identity(kind,src[key],sid)
            entry=enqueue(sid,{**data['body'],'mixId':body.get('mixId')})
            if data['body'].get('collectionId'): catalog.member(data['body']['collectionId'],sid,body.get('mixId'))
            shutil.rmtree(token_dir(token),ignore_errors=True);catalog.pending(token,delete=True)
            await changed();return {'entry':entry}
        if action=='replace': return await commit_import(data,replace=data['match'])
        if action=='separate' and not data.get('exact'): return await commit_import(data)
        raise HTTPException(400,'Choose how to use this song')


def promote(sid,args):
    """Only called after a successful worker exit; old media paths stay valid."""
    staging=Path(args['staging']); src=staging/'songs'/sid
    fresh=store._read(src/'song.json')
    if not fresh or fresh.get('status')!='ready': raise RuntimeError('Replacement did not finish; original retained')
    old=require_song(sid); revision=args['revision']
    catalog.upsert(old)  # Freeze the prior source metadata before changing the pointer.
    target=store.song_dir(sid)/'versions'/revision
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.move(str(src),str(target))
    fresh.update(id=sid,assetVersion=revision,created=old['created'],title=old.get('title'),artist=old.get('artist'),
                 thumbnail=old.get('thumbnail') or fresh.get('thumbnail'),replacementError=None)
    if not fresh.get('thumbnail') and (target/'artwork.jpg').is_file():
        shutil.copy2(target/'artwork.jpg',store.song_dir(sid)/'artwork.jpg')
        fresh['thumbnail']=f'/artwork/{sid}'
    for key in ('titleOverride','artistOverride'):
        if key in old: fresh[key]=old[key]
    store.write_song(sid,fresh)
    with catalog.db() as c: c.execute('UPDATE mixes SET review=1 WHERE song_id=?',(sid,))
    if args.get('fingerprint'): catalog.fingerprint(sid,args['fingerprint'])
    catalog.replacement(sid,clear=True)
    shutil.rmtree(staging,ignore_errors=True)
