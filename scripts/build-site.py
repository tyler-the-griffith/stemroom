"""Build stable website entry links that open the current room's Queue directly."""
from html import escape
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

root = Path(__file__).resolve().parent.parent
docs = root / 'docs'
room = json.loads((docs / 'room.json').read_text())
origin = room.get('url', '')
url = urlsplit(origin)
if (url.scheme != 'https' or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.path not in ('', '/', '/stemroom', '/stemroom/')):
    raise ValueError('Configure an HTTPS app URL with scripts/set-room.py first')
queue = origin.rstrip('/') + '/#/'
target = escape(queue, quote=True)
script_target = json.dumps(queue).replace('<', r'\u003c')

assets = docs / 'assets'
assets.mkdir(parents=True, exist_ok=True)
for source, name in [('web/js/themes.js', 'themes.js'), ('web/favicon.svg', 'favicon.svg')]:
    shutil.copyfile(root / source, assets / name)
palette = (root / 'web/css/app.css').read_text().split('* { box-sizing:', 1)[0]
(assets / 'palette.css').write_text(palette.rstrip() + '\n')
(docs / 'index.html').write_text(f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Open the Stemroom Queue to pick a song, browse your library, and sing together.">
  <meta name="theme-color" content="#18210F">
  <title>Stemroom · Queue</title>
  <script>window.location.replace({script_target});</script>
  <meta http-equiv="refresh" content="0; url={target}">
  <link rel="icon" href="assets/favicon.svg" type="image/svg+xml">
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; background: #18210F; color: #FEFAE0; }}
    body {{ margin: 0; padding: 2rem; }}
    main {{ max-width: 32rem; margin: 15vh auto; }}
    a {{ color: #DDA15E; }}
  </style>
</head>
<body>
  <main>
    <h1>Opening Stemroom…</h1>
    <p><a id="queue-link" href="{target}">Open the Queue</a></p>
    <p><a href="https://tylergriffith.us/">← Tyler Griffith</a></p>
  </main>
</body>
</html>
''')
print('Queue entry page and theme assets prepared')
