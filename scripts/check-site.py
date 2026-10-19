"""Check local assets and HTTPS room configuration before publishing Pages."""
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parent.parent / 'docs'

class Links(HTMLParser):
    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key not in ('src', 'href') or not value: continue
            url = urlsplit(value)
            if url.scheme or url.netloc or not url.path: continue
            assert not url.path.startswith('/'), f'Root-relative path breaks project Pages: {value}'
            assert (root / url.path).exists(), f'Missing site asset: {value}'

Links().feed((root / 'index.html').read_text())
room = json.loads((root / 'room.json').read_text())
if room.get('url'):
    url = urlsplit(room['url'])
    assert url.scheme == 'https' and url.hostname and not url.username and not url.password
    assert not url.query and not url.fragment, 'Never put access secrets in the public room URL'
print('Website links and configuration passed')
