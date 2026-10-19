import test from 'node:test';
import assert from 'node:assert/strict';
import {appUrl, roomBase} from '../web/js/urls.mjs';
import {readConnection, validBackend} from '../web/js/connection.mjs';

test('local installs and separately hosted frontends resolve all room resources', () => {
  for (const [frontend, backend, expected] of [
    ['https://room.test/', '', 'https://room.test/'],
    ['https://site.test/stemroom/', '', 'https://site.test/stemroom/'],
    ['https://site.test/stemroom/', 'https://backend.test', 'https://backend.test/'],
    ['https://site.test/stemroom/', 'https://backend.test/stemroom/', 'https://backend.test/stemroom/'],
  ]) {
    const base = roomBase(frontend, backend);
    for (const path of ['/api/state','/ws','/media/song/lead.opus?v=2','/artwork/song','/exports/mix.flac']) {
      assert.equal(appUrl(path, base), expected + path.slice(1));
    }
    assert.equal(appUrl('https://images.test/cover.jpg', base), 'https://images.test/cover.jpg');
    assert.equal(appUrl('//images.test/cover.jpg', base), '//images.test/cover.jpg');
  }
});

test('runtime discovery validates addresses and keeps failures recoverable', async () => {
  assert.equal(validBackend('https://new.trycloudflare.com/'), 'https://new.trycloudflare.com');
  for (const invalid of ['http://insecure.test', 'https://user:secret@example.com', 'https://room.test?token=x', 'javascript:alert(1)', null]) {
    assert.equal(validBackend(invalid), null);
  }
  assert.equal(await readConnection('https://site.test/stemroom/backend.json', async (url, options) => {
    assert.equal(options.cache, 'no-store');
    return Response.json({backendUrl: 'https://new.trycloudflare.com'});
  }), 'https://new.trycloudflare.com');
  assert.equal(await readConnection('https://site.test/backend.json', async () => new Response('', {status: 404})), null);
  assert.equal(await readConnection('https://site.test/backend.json', async () => {throw new Error('offline')}), null);
  assert.equal(await readConnection('https://site.test/backend.json', async () => Response.json({backendUrl: 'http://bad.test'})), null);
});
