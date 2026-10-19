"""Record a verified public room URL; contains no access token or credentials."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url', help='Protected HTTPS root URL printed by the tunnel')
args = parser.parse_args()
url = urlsplit(args.url)
if (url.scheme != 'https' or not url.hostname or url.username or url.password
        or url.query or url.fragment or url.path not in ('', '/')):
    parser.error('Use a plain HTTPS origin, with no path, credentials, query, or fragment')
root = Path(__file__).resolve().parent.parent
data = {'url': args.url.rstrip('/'),
        'note': 'Hosted on Tyler’s computer. It must be online; access is restricted to invited email addresses.'}
(root / 'docs/room.json').write_text(json.dumps(data, indent=2) + '\n')
print('Room URL updated. Publish docs/ to update both website entry points.')
