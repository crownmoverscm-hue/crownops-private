"""Priority 4: two disposable, independent company installations. Fictional data only.

Each installation runs in its own process (as each company does in production) with
its own database, secret key and session. Both complete lead -> estimate -> client
acceptance -> deposit -> booking -> completed job -> issued invoice through the API,
then both are served by live HTTP servers to prove nothing crosses between them.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
JUNK = {k: [k.title(), p] for k, p in (('single', '90'), ('quarter', '180'), ('half', '320'), ('three_quarter', '450'), ('full', '600'))}
CONFIGS = {
    'A': {'token': 'alpha-session', 'owner_email': 'owner@alpha.example.invalid', 'language': 'English', 'address': '10 Fictional Road, Ottawa',
          'taxes': [{'name': 'HST', 'rate_percent': 13.0, 'registration': 'FICTIONAL-A'}],
          'profile': {'name': 'Alpha Fictional Movers', 'phone': '613-555-0101', 'email': 'office@alpha.example.invalid', 'timezone': 'America/Toronto', 'local_time_label': 'Ottawa time'},
          'rates': {'2': 12000, '3': 15500, '4': 19000, '5': 23000, '6': 27000},
          'pricing': {'truck_minimums': [[6, 3], [4, 2]], 'default_minimum_hours': '3', 'moving_packing_minimum_hours': '3', 'small_afternoon_minimum_hours': '2',
                      'local_fuel_per_truck': '30', 'extra_truck_fee': '20', 'long_distance_km_rate': '1', 'long_distance_km_buffer': '1.05',
                      'long_distance_transport_multiplier': '1.1', 'junk_extra_labour': '60', 'default_deposit': '200', 'limited_liability_text': 'Alpha released value', 'junk_tiers': JUNK}},
    'B': {'token': 'beta-session', 'owner_email': 'owner@beta.example.invalid', 'language': 'French', 'address': '20 Fictional Way, Vancouver',
          'taxes': [{'name': 'GST', 'rate_percent': 5.0, 'registration': 'FICTIONAL-B'}, {'name': 'PST', 'rate_percent': 7.0, 'registration': 'FICTIONAL-B2'}],
          'profile': {'name': 'Beta Fictional Relocation', 'phone': '604-555-0202', 'email': 'office@beta.example.invalid', 'timezone': 'America/Vancouver',
                      'local_time_label': 'Vancouver time', 'local_time_label_fr': 'heure de Vancouver'},
          'rates': {'2': 14000, '3': 18000, '4': 22000, '5': 26000, '6': 30000},
          'pricing': {'truck_minimums': [[5, 2]], 'default_minimum_hours': '2', 'moving_packing_minimum_hours': '2', 'small_afternoon_minimum_hours': '1.5',
                      'local_fuel_per_truck': '55', 'extra_truck_fee': '35', 'long_distance_km_rate': '1.2', 'long_distance_km_buffer': '1.1',
                      'long_distance_transport_multiplier': '1.2', 'junk_extra_labour': '70', 'default_deposit': '300', 'limited_liability_text': 'Beta valeur declaree', 'junk_tiers': JUNK}},
}


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


def get(url, token=None):
    req = urllib.request.Request(url, headers={'Cookie': 'crownops_session=' + token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class IndependentInstallationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.env, cls.db, cls.result, cls.servers, cls.ports = {}, {}, {}, {}, {}
        for name, cfg in CONFIGS.items():
            folder = base / name; folder.mkdir()
            (folder / 'config.json').write_text(json.dumps(cfg))
            cls.db[name] = folder / 'crownops.sqlite3'
            cls.env[name] = {**os.environ, 'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox',
                             'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode(), 'CROWNOPS_UPLOADS': str(folder / 'uploads')}
            run = subprocess.run([sys.executable, '-I', str(ROOT / 'tests' / 'installation_workflow.py'), str(cls.db[name]), str(folder / 'config.json')],
                                 capture_output=True, text=True, env=cls.env[name], cwd=ROOT, timeout=300)
            assert run.returncode == 0, name + ' workflow failed: ' + run.stderr[-2000:]
            cls.result[name] = json.loads(run.stdout.strip().splitlines()[-1])
        for name in CONFIGS:
            cls.ports[name] = free_port()
            cls.servers[name] = subprocess.Popen([sys.executable, str(ROOT / 'manage.py'), '--db', str(cls.db[name]), 'serve', '--host', '127.0.0.1', '--port', str(cls.ports[name])],
                                                 env=cls.env[name], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for name in CONFIGS:
            for _ in range(200):
                try:
                    if get(cls.url(name, '/api/health'))[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(name + ' server did not start')

    @classmethod
    def tearDownClass(cls):
        for proc in cls.servers.values():
            proc.terminate(); proc.wait(timeout=20)
        cls.temp.cleanup()

    @classmethod
    def url(cls, name, path):
        return f'http://127.0.0.1:{cls.ports[name]}{path}'

    def test_each_installation_completes_lead_to_invoice_with_its_own_settings(self):
        for name, cfg in CONFIGS.items():
            r = self.result[name]
            with self.subTest(installation=name):
                rate = Decimal(cfg['rates']['2']) / 100; fuel = Decimal(cfg['pricing']['local_fuel_per_truck'])
                hours = max(Decimal(3), Decimal(cfg['pricing']['moving_packing_minimum_hours']))
                self.assertEqual(r['quote_low_cents'], int((rate * hours + rate * 1 + fuel) * 100))
                self.assertEqual((r['fuel_cents'], r['pricing_policy_version']), (int(fuel * 100), 1))
                self.assertFalse(r['accepted_booking_confirmed'])  # acceptance alone never books
                self.assertEqual(r['invoice_status'], 'Issued'); self.assertEqual(r['invoice_issuer'], cfg['profile']['name'])
                subtotal = rate * Decimal('3.5') + rate + fuel
                taxes = sum(int((subtotal * 100 * Decimal(str(t['rate_percent'])) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) for t in cfg['taxes'])
                self.assertEqual(r['invoice_total_cents'], int(subtotal * 100) + taxes)

    def test_bookings_use_each_company_zone_for_the_same_local_arrival(self):
        for name, cfg in CONFIGS.items():
            r = self.result[name]
            with self.subTest(installation=name):
                self.assertEqual(r['zone'], cfg['profile']['timezone'])
                start = datetime.fromisoformat(r['job_start_utc']).astimezone(ZoneInfo(cfg['profile']['timezone']))
                self.assertEqual((start.date().isoformat(), start.strftime('%H:%M')), ('2026-11-20', '09:00'))
        self.assertNotEqual(self.result['A']['job_start_utc'], self.result['B']['job_start_utc'])

    def test_client_messages_and_pages_carry_only_their_own_identity(self):
        for name, other in (('A', 'B'), ('B', 'A')):
            r, mine, theirs = self.result[name], CONFIGS[name]['profile'], CONFIGS[other]['profile']
            with self.subTest(installation=name):
                self.assertTrue(r['message_subjects'])
                self.assertIn(mine['name'], r['message_text']); self.assertIn(mine['phone'], r['message_text'])
                for leak in (theirs['name'], theirs['phone'], 'Crown Movers', '514-606-4030'):
                    self.assertNotIn(leak, r['message_text'] + r['portal_page'])
        self.assertIn('Votre devis', self.result['B']['message_text'])

    def test_live_servers_are_isolated_and_branded_independently(self):
        for name, other in (('A', 'B'), ('B', 'A')):
            with self.subTest(installation=name):
                status, body = get(self.url(name, '/api/company/public-profile'))
                self.assertEqual((status, json.loads(body)['name']), (200, CONFIGS[name]['profile']['name']))
                self.assertIn(CONFIGS[name]['profile']['timezone'], get(self.url(name, '/business-zone.js'))[1])
                self.assertEqual(get(self.url(name, '/api/me'), CONFIGS[name]['token'])[0], 200)
                self.assertEqual(get(self.url(name, '/api/me'), CONFIGS[other]['token'])[0], 401)  # the other company's session is unknown here
                page = get(self.url(name, '/static/client.js'))[1]
                self.assertNotIn(CONFIGS[other]['profile']['name'], page); self.assertNotIn('Crown Movers', page)

    def test_databases_are_separate_files_with_disjoint_records(self):
        self.assertNotEqual(self.db['A'].resolve(), self.db['B'].resolve())
        names = {}
        for name in CONFIGS:
            with sqlite3.connect(self.db[name]) as db:
                names[name] = {r[0] for r in db.execute('SELECT client_name FROM leads')}
                self.assertEqual(db.execute("SELECT count(*) FROM users").fetchone()[0], 1)
        self.assertTrue(names['A']) ; self.assertTrue(names['B']); self.assertFalse(names['A'] & names['B'])


if __name__ == '__main__':
    unittest.main()
