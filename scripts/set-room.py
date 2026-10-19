"""Record a verified public room URL; contains no access token or credentials."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url', help='HTTPS app URL, optionally ending in /stemroom/')
parser.add_argument('--public', action='store_true', help='Describe a room open to everyone without sign-in')
args = parser.parse_args()
url = urlsplit(args.url)
if (url.scheme != 'https' or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.path not in ('', '/', '/stemroom', '/stemroom/')):
    parser.error('Use an HTTPS app URL, optionally ending in /stemroom/, without credentials, query, or fragment')
root = Path(__file__).resolve().parent.parent
access = ('Anyone can join without signing in and control the shared library and player.' if args.public
          else 'Access is restricted to invited email addresses.')
data = {'url': args.url.rstrip('/'),
        'note': f'Song processing and the shared library require Tyler’s computer to be online. {access}'}
(root / 'docs/room.json').write_text(json.dumps(data, indent=2) + '\n')
subprocess.run([sys.executable, str(root / 'scripts/build-site.py')], check=True)
print('Room URL updated. Publish docs/ to update the GitHub Pages entry link.')
