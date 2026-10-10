"""Verify the public install assets are usable on Apple and Android launchers."""
from html.parser import HTMLParser
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

STATIC = Path(__file__).resolve().parents[1] / 'app' / 'static'


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.links = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            self.links.append(dict(attrs))


def png_dimensions(path):
    raw = path.read_bytes()
    if raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR':
        raise AssertionError(f'{path.name} is not a PNG')
    width, height, depth, colour = struct.unpack('>IIBB', raw[16:26])
    # Opaque RGB avoids transparent launcher corners on Apple devices.
    if (depth, colour) != (8, 2):
        raise AssertionError(f'{path.name} must be an opaque RGB PNG')
    return width, height


class DeviceAssetTests(unittest.TestCase):
    def test_manifest_has_real_opaque_launcher_assets(self):
        manifest = json.loads((STATIC / 'manifest.webmanifest').read_text())
        self.assertEqual(manifest['start_url'], '/')
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        available = set()
        for icon in manifest['icons']:
            if icon['type'] != 'image/png':
                continue
            self.assertTrue(icon['src'].startswith('/static/'))
            size = png_dimensions(STATIC / icon['src'].removeprefix('/static/'))
            self.assertEqual(icon['sizes'], f'{size[0]}x{size[1]}')
            available.add((size, icon['purpose']))
        self.assertTrue({((192, 192), 'any'), ((512, 512), 'any'),
                         ((512, 512), 'maskable')}.issubset(available))

    def test_apple_touch_icon_and_favicon_reference_existing_pngs(self):
        links = Links((STATIC / 'index.html').read_text()).links
        apple = next(link for link in links if link.get('rel') == 'apple-touch-icon')
        self.assertEqual(png_dimensions(STATIC / apple['href'].removeprefix('/static/')), (180, 180))
        favicon = next(link for link in links if link.get('rel') == 'icon' and link.get('type') == 'image/png')
        self.assertEqual(png_dimensions(STATIC / favicon['href'].removeprefix('/static/')), (32, 32))


@unittest.skipUnless(importlib.util.find_spec('fastapi') and importlib.util.find_spec('httpx'),
                     'Web dependencies unavailable locally')
class DeviceDeliveryTests(unittest.TestCase):
    def test_browser_install_metadata_is_public_and_revalidated(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0'
        }):
            with TestClient(create_app(Path(folder) / 'device.sqlite3')) as client:
                manifest = client.get('/manifest.webmanifest')
                self.assertEqual(manifest.status_code, 200)
                self.assertEqual(manifest.headers['cache-control'], 'no-cache')
                self.assertIn('application/manifest+json', manifest.headers['content-type'])
                for icon in manifest.json()['icons']:
                    response = client.get(icon['src'])
                    self.assertEqual(response.status_code, 200, icon['src'])
                    self.assertIn(icon['type'], response.headers['content-type'])
                sw = client.get('/sw.js')
                self.assertEqual(sw.status_code, 200)
                self.assertEqual(sw.headers['service-worker-allowed'], '/')
                self.assertEqual(sw.headers['cache-control'], 'no-cache')
                # Public icon metadata must not make operational records public.
                self.assertEqual(client.get('/api/company/contacts').status_code, 401)
                self.assertEqual(client.get('/api/company/fleet').status_code, 401)


if __name__ == '__main__':
    unittest.main()
