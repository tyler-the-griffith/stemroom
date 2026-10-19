import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import json
import shlex

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('frontend', ROOT / 'scripts/build-frontend.py')
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)
online_spec = importlib.util.spec_from_file_location('online', ROOT / 'scripts/online-service.py')
online = importlib.util.module_from_spec(online_spec)
online_spec.loader.exec_module(online)


class FrontendBuildTests(unittest.TestCase):
    def test_static_bundle_keeps_browser_assets_and_excludes_private_library(self):
        with tempfile.TemporaryDirectory() as output:
            path = frontend.build(output, 'https://room.example/')
            for name in ['index.html', 'js/main.js', 'js/engine.js', 'vendor/SignalsmithStretch.mjs', 'css/app.css', 'favicon.svg']:
                self.assertTrue((path / name).is_file(), name)
            self.assertNotIn('location.replace', (path / 'index.html').read_text())
            self.assertIn('"backendUrl": "https://room.example"', (path / 'connection.js').read_text())
            self.assertFalse((path / 'data').exists())
            self.assertFalse((path / 'server').exists())
            frontend.build(output, 'https://permanent.example')
            self.assertNotIn('room.example', (path / 'connection.js').read_text())
            frontend.build(output, 'https://permanent.example', runtime=True)
            self.assertIn('"runtimeConfig": "backend.json"', (path / 'connection.js').read_text())

    def test_backend_validation(self):
        with tempfile.TemporaryDirectory() as output:
            for value in ['http://public.example', 'https://user:secret@room.example', 'https://room.example/?key=x', 'https://room.example/other']:
                with self.assertRaises(ValueError):
                    frontend.build(output, value)

    def test_tunnel_publication_uses_atomic_config_and_stdin(self):
        path = "website with spaces/stemroom"
        self.assertIn(shlex.quote(path + '/backend.json'), online.publish_command(path))
        with patch.object(online.subprocess, 'run') as run:
            online.publish('site@example.test', path, 'https://example-room.trycloudflare.com')
            args, kwargs = run.call_args
            self.assertEqual(args[0][-2], 'site@example.test')
            self.assertEqual(json.loads(kwargs['input']), {'backendUrl':'https://example-room.trycloudflare.com'})
            self.assertIn('StrictHostKeyChecking=yes', args[0])
            self.assertIn('mv -- ', args[0][-1])
            with self.assertRaises(ValueError):
                online.publish('site@example.test', path, 'https://unrelated.test')
