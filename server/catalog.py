"""Transactional catalog. Audio/analysis stay on disk; references never copy audio."""
import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from . import config

@contextmanager
def db():
    config.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DATA / 'catalog.sqlite3', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()

def initialize():
    with db() as c:
        c.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS songs(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            id TEXT NOT NULL, metadata TEXT NOT NULL, PRIMARY KEY(song_id,id));
        CREATE TABLE IF NOT EXISTS mixes(id TEXT PRIMARY KEY, song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
            name TEXT NOT NULL, settings TEXT NOT NULL, version TEXT NOT NULL, review INTEGER NOT NULL DEFAULT 0,
            updated REAL NOT NULL, UNIQUE(song_id,name));
        CREATE TABLE IF NOT EXISTS defaults(song_id TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
            mix_id TEXT NOT NULL REFERENCES mixes(id));
        CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS members(collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE,
            song_id TEXT REFERENCES songs(id) ON DELETE CASCADE, mix_id TEXT REFERENCES mixes(id) ON DELETE SET NULL,
            position INTEGER NOT NULL, PRIMARY KEY(collection_id,song_id));
        CREATE TABLE IF NOT EXISTS identities(kind TEXT NOT NULL, value TEXT NOT NULL,
            song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE, PRIMARY KEY(kind,value));
        CREATE TABLE IF NOT EXISTS fingerprints(song_id TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
            data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents(key TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(token TEXT PRIMARY KEY, data TEXT NOT NULL);
        ''')

def upsert(song):
    with db() as c:
        c.execute('INSERT INTO songs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata',
                  (song['id'], json.dumps(song)))
        # Initial version is updated while it is being prepared, then frozen on replacement.
        version = song.get('assetVersion', 'original')
        c.execute('INSERT INTO versions VALUES(?,?,?) ON CONFLICT(song_id,id) DO UPDATE SET metadata=excluded.metadata',
                  (song['id'], version, json.dumps(song)))
        source = song.get('source', {})
        for kind, val in [('youtube', source.get('videoId')), ('sha256', source.get('sha256'))]:
            if val:
                c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?)', (kind,val,song['id']))

def version(song_id, version_id):
    with db() as c:
        r = c.execute('SELECT metadata FROM versions WHERE song_id=? AND id=?', (song_id,version_id)).fetchone()
        return json.loads(r[0]) if r else None

def identify(kind, value):
    with db() as c:
        r = c.execute('SELECT song_id FROM identities WHERE kind=? AND value=?', (kind,value)).fetchone()
        return r[0] if r else None

def identity(kind, value, song_id):
    with db() as c:
        c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?)', (kind,value,song_id))

def document(key, data=None):
    with db() as c:
        if data is not None:
            c.execute('INSERT INTO documents VALUES(?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data', (key,json.dumps(data)))
        r = c.execute('SELECT data FROM documents WHERE key=?', (key,)).fetchone()
        return json.loads(r[0]) if r else None

def unpack_mix(row):
    return dict(id=row['id'], songId=row['song_id'], name=row['name'], settings=json.loads(row['settings']),
                assetVersion=row['version'], needsReview=bool(row['review']), updated=row['updated'])

def mixes(song_id):
    with db() as c:
        return [unpack_mix(r) for r in c.execute('SELECT * FROM mixes WHERE song_id=? ORDER BY updated,id', (song_id,))]

def get_mix(song_id, mix_id=None):
    with db() as c:
        if not mix_id:
            r=c.execute('SELECT mix_id FROM defaults WHERE song_id=?', (song_id,)).fetchone()
            mix_id=r[0] if r else None
        r=c.execute('SELECT * FROM mixes WHERE song_id=? AND id=?', (song_id,mix_id)).fetchone()
        return unpack_mix(r) if r else None

def validate_settings(settings):
    if not isinstance(settings,dict): raise ValueError('Mix settings must be an object')
    def number(obj,key,lo,hi):
        if key not in obj: return
        v=obj[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{key} must be between {lo} and {hi}')
    number(settings,'master',0,1.5)
    performance=settings.get('performance',{})
    if not isinstance(performance,dict): raise ValueError('Invalid performance settings')
    for key,lo,hi in [('key',-7,7),('tempo',.6,1.4),('guide',0,1)]: number(performance,key,lo,hi)
    stems=settings.get('stems',{})
    if not isinstance(stems,dict): raise ValueError('Invalid stem settings')
    for stem in stems.values():
        if not isinstance(stem,dict): raise ValueError('Invalid stem settings')
        for key,lo,hi in [('vol',0,1.5),('pan',-1,1),('reverb',0,1),('echo',0,1)]: number(stem,key,lo,hi)
        if stem.get('tone','natural') not in ('natural','warm','bright','radio','thin'): raise ValueError('Invalid tone')
        if 'followLead' in stem and not isinstance(stem['followLead'],bool): raise ValueError('followLead must be a boolean')
    duet=settings.get('duet',[])
    if not isinstance(duet,list) or any(v not in (None,0,1,2,3,'all') for v in duet): raise ValueError('Invalid duet parts')

def save_mix(song_id, name, settings, version, mix_id=None, default=False):
    name = str(name or '').strip()[:80]
    if not name: raise ValueError('Give this mix a name')
    validate_settings(settings)
    with db() as c:
        if mix_id and not c.execute('SELECT 1 FROM mixes WHERE song_id=? AND id=?', (song_id,mix_id)).fetchone():
            raise ValueError('Mix not found')
        mix_id = mix_id or uuid.uuid4().hex[:16]
        try:
            c.execute('''INSERT INTO mixes VALUES(?,?,?,?,?,0,?) ON CONFLICT(id) DO UPDATE SET
                      name=excluded.name, settings=excluded.settings, version=excluded.version, review=0, updated=excluded.updated''',
                      (mix_id,song_id,name,json.dumps(settings),version,time.time()))
        except sqlite3.IntegrityError as e:
            raise ValueError('A mix with that name already exists') from e
        if default or not c.execute('SELECT 1 FROM defaults WHERE song_id=?', (song_id,)).fetchone():
            c.execute('INSERT INTO defaults VALUES(?,?) ON CONFLICT(song_id) DO UPDATE SET mix_id=excluded.mix_id', (song_id,mix_id))
    return get_mix(song_id,mix_id)

def default_mix(song_id, mix_id):
    if not get_mix(song_id,mix_id): raise ValueError('Mix not found')
    with db() as c:
        c.execute('INSERT INTO defaults VALUES(?,?) ON CONFLICT(song_id) DO UPDATE SET mix_id=excluded.mix_id', (song_id,mix_id))

def collections():
    with db() as c:
        return [dict(id=r['id'], name=r['name'], count=r['count']) for r in c.execute('''SELECT c.*,count(m.song_id) AS count
            FROM collections c LEFT JOIN members m ON c.id=m.collection_id GROUP BY c.id ORDER BY c.position,c.id''')]

def collection_save(name, cid=None):
    name=str(name or '').strip()[:80]
    if not name: raise ValueError('Give this collection a name')
    with db() as c:
        if cid:
            if not c.execute('SELECT 1 FROM collections WHERE id=?', (cid,)).fetchone(): raise ValueError('Collection not found')
            c.execute('UPDATE collections SET name=? WHERE id=?', (name,cid))
        else:
            cid=uuid.uuid4().hex[:12]
            c.execute('INSERT INTO collections VALUES(?,?,(SELECT coalesce(max(position),-1)+1 FROM collections))', (cid,name))
    return {'id':cid,'name':name}

def members(cid):
    with db() as c:
        return [dict(songId=r['song_id'],mixId=r['mix_id']) for r in c.execute('SELECT * FROM members WHERE collection_id=? ORDER BY position,song_id', (cid,))]

def member(cid,song_id,mix_id=None,remove=False):
    if mix_id and not get_mix(song_id,mix_id): raise ValueError('That mix belongs to another song')
    with db() as c:
        if remove:
            c.execute('DELETE FROM members WHERE collection_id=? AND song_id=?', (cid,song_id))
        else:
            try:
                c.execute('''INSERT INTO members VALUES(?,?,?,(SELECT coalesce(max(position),-1)+1 FROM members WHERE collection_id=?))
                    ON CONFLICT(collection_id,song_id) DO UPDATE SET mix_id=excluded.mix_id''', (cid,song_id,mix_id,cid))
            except sqlite3.IntegrityError as e: raise ValueError('Song or collection not found') from e

def order(cid, ids):
    with db() as c:
        existing = [r[0] for r in c.execute('SELECT song_id FROM members WHERE collection_id=?', (cid,))] if cid else [r[0] for r in c.execute('SELECT id FROM collections')]
        if len(ids)!=len(set(ids)) or set(ids)!=set(existing): raise ValueError('Order must include every item exactly once')
        for pos, sid in enumerate(ids):
            if cid: c.execute('UPDATE members SET position=? WHERE collection_id=? AND song_id=?', (pos,cid,sid))
            else: c.execute('UPDATE collections SET position=? WHERE id=?', (pos,sid))

def collection_delete(cid):
    with db() as c: c.execute('DELETE FROM collections WHERE id=?', (cid,))

def song_delete(sid):
    with db() as c:
        c.execute('DELETE FROM defaults WHERE song_id=?', (sid,))
        c.execute('DELETE FROM songs WHERE id=?', (sid,))

def pending(token, value=None, delete=False):
    with db() as c:
        if delete: c.execute('DELETE FROM imports WHERE token=?', (token,)); return
        if value is not None: c.execute('INSERT OR REPLACE INTO imports VALUES(?,?)', (token,json.dumps(value)))
        r=c.execute('SELECT data FROM imports WHERE token=?', (token,)).fetchone()
        return json.loads(r[0]) if r else None

def fingerprint(sid=None, value=None):
    with db() as c:
        if value is not None:
            c.execute('INSERT OR REPLACE INTO fingerprints VALUES(?,?)', (sid,json.dumps(value)))
        return {r[0]:json.loads(r[1]) for r in c.execute('SELECT * FROM fingerprints')}


def replacement(sid, args=None, clear=False):
    with db() as c:
        key='replacement:'+str(sid)
        if clear: c.execute('DELETE FROM documents WHERE key=?',(key,))
        elif args is not None: c.execute('INSERT OR REPLACE INTO documents VALUES(?,?)',(key,json.dumps(args)))
        return {r[0].split(':',1)[1]:json.loads(r[1]) for r in c.execute("SELECT key,data FROM documents WHERE key LIKE 'replacement:%'")}
