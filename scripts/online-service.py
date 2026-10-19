"""Run a public Quick Tunnel and announce its URL to an existing SSH web host.

Only a tiny public backend.json file is uploaded; no library or credentials.
Requires STEMROOM_PUBLISH_SSH and STEMROOM_PUBLISH_DIR. Run under systemd with
Restart=always. Named tunnels can replace this helper after DNS is configured.
"""
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import threading
from urllib.request import urlopen

TUNNEL_URL = re.compile(r'https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b')


def publish_command(directory):
    # All remote paths are single shell arguments; stdin carries the JSON bytes.
    directory = directory.rstrip('/')
    if not directory or '\n' in directory or '\r' in directory:
        raise ValueError('Invalid destination directory')
    target = directory + '/backend.json'
    temporary = directory + '/.backend.json.tmp'
    return (f'umask 022; cat > {shlex.quote(temporary)} && '
            f'mv -- {shlex.quote(temporary)} {shlex.quote(target)}')


def publish(host, directory, url):
    if not TUNNEL_URL.fullmatch(url):
        raise ValueError('Expected a Cloudflare Quick Tunnel URL')
    if not host or host.startswith('-'):
        raise ValueError('Invalid SSH host')
    body = json.dumps({'backendUrl': url}).encode() + b'\n'
    subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                    '-o', 'StrictHostKeyChecking=yes', host, publish_command(directory)],
                   input=body, check=True, timeout=30)


def main():
    root = Path(__file__).resolve().parent.parent
    host = os.environ['STEMROOM_PUBLISH_SSH']
    directory = os.environ['STEMROOM_PUBLISH_DIR']
    port = int(os.environ.get('STEMROOM_PORT', '8765'))
    executable = os.environ.get('CLOUDFLARED', str(root / 'data/tools/cloudflared'))
    process = subprocess.Popen([executable, 'tunnel', '--no-autoupdate', '--url',
                                f'http://127.0.0.1:{port}'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    stop = threading.Event()

    def shutdown(*_):
        stop.set()
        if process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    def announce(url):
        while not stop.is_set() and process.poll() is None:
            try:
                # Announce only once the new tunnel actually serves this app.
                with urlopen(url + '/api/info', timeout=10) as response:
                    info = json.load(response)
                if not isinstance(info.get('joinUrl'), str):
                    raise ValueError('Backend not ready')
                if stop.is_set():
                    return
                publish(host, directory, url)
                print('Published backend address to website', flush=True)
                return
            except Exception as error:
                print(f'Backend announcement will retry: {type(error).__name__}', flush=True)
                stop.wait(10)

    worker = None
    try:
        for line in process.stdout:
            print(line, end='', flush=True)
            found = TUNNEL_URL.search(line)
            if found and worker is None:
                worker = threading.Thread(target=announce, args=(found.group(),), daemon=True)
                worker.start()
        code = process.wait()
    finally:
        shutdown()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        if worker:
            worker.join(timeout=35)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
