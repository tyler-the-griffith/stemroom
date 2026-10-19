"""Publish only browser files to an existing static host, with a separate room server."""
import argparse
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def build(destination, backend, runtime=False):
    url = urlsplit(backend)
    local = url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1')
    if (url.scheme != 'https' and not local) or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Backend must be HTTPS (or localhost for preview), without credentials or query parameters')
    if url.path not in ('', '/', '/stemroom', '/stemroom/'):
        raise ValueError('Backend path must be / or /stemroom/')
    destination = Path(destination).resolve()
    source = ROOT / 'web'
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('Build outside the source tree')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    settings = {'backendUrl': backend.rstrip('/')}
    if runtime:
        settings['runtimeConfig'] = 'backend.json'
    config = json.dumps(settings, ensure_ascii=True)
    (destination / 'connection.js').write_text(f'window.StemroomConnection = Object.freeze({config});\n')
    (destination / '.nojekyll').touch()
    # Revalidate small app files, particularly the backend address, after deploys.
    # Do not rely on shared-host MIME defaults for ES modules or the audio WASM.
    (destination / '.htaccess').write_text('''DirectoryIndex index.html
AddType text/javascript .mjs
AddType application/wasm .wasm
<IfModule mod_headers.c>
  Header set Cache-Control "no-cache"
</IfModule>
''')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--backend', required=True)
    parser.add_argument('--runtime', action='store_true', help='Discover restarted tunnels using backend.json')
    args = parser.parse_args()
    print(f'Browser frontend prepared: {build(args.output, args.backend, args.runtime)}')
