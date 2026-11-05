"""Commercial pilot validation (Priorities 3 and 8): two installations provisioned only with the
documented operator commands (docs/COMMERCIAL_DEPLOYMENT_RUNBOOK.md) and the owner-facing HTTP API.
No database row is written directly. Fictional data; external actions are off.

Each company: explicit identity -> company profile -> one-time setup code -> live server ->
owner web setup -> sign-in -> pricing, rates, billing/tax, booking terms, template, logo ->
crew + truck + staff invitation -> website inquiry -> estimate -> client acceptance -> deposit ->
crew/truck plan -> job completed -> invoice issued -> field payment collected -> manager verification
-> scheduled backup -> db-status and ops-check.
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from cryptography.fernet import Fernet
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
JUNK = {k: [k.title(), p] for k, p in (('single', '90'), ('quarter', '180'), ('half', '320'), ('three_quarter', '450'), ('full', '600'))}
COMPANIES = {
    'north': {'name': 'Northern Fictional Moving', 'phone': '807-555-0110', 'email': 'office@north.example.invalid', 'timezone': 'America/Winnipeg',
              'language': 'English', 'rate': 125, 'fuel': '40', 'tax': [{'name': 'GST', 'rate_percent': 5.0, 'registration': 'FICT-N1'}],
              'owner': 'owner@north.example.invalid', 'sales': 'sales@north.example.invalid'},
    'coast': {'name': 'Coastal Fictional Relocation', 'phone': '902-555-0120', 'email': 'office@coast.example.invalid', 'timezone': 'America/Halifax',
              'language': 'French', 'rate': 150, 'fuel': '60', 'tax': [{'name': 'HST', 'rate_percent': 15.0, 'registration': 'FICT-C1'}],
              'owner': 'owner@coast.example.invalid', 'sales': 'sales@coast.example.invalid'},
}
PASSWORD = 'fictional-pass-' + 'x' * 8


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0)); return s.getsockname()[1]


class Installation:
    def __init__(self, key, base):
        self.key, self.cfg = key, COMPANIES[key]
        self.dir = base / key; self.dir.mkdir()
        self.db = self.dir / 'data' / 'crownops.sqlite3'; self.db.parent.mkdir()
        self.env = {**os.environ, 'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'CROWNOPS_SECRET_KEY': Fernet.generate_key().decode(),
                    'CROWNOPS_UPLOADS': str(self.dir / 'data' / 'uploads'), 'CROWNOPS_BACKUPS': str(self.dir / 'backups'),
                    'CROWNOPS_DISK_WARN_PERCENT': '0', 'CROWNOPS_DISK_CRITICAL_PERCENT': '0'}

    def manage(self, *args, ok=True):
        run = subprocess.run([sys.executable, str(ROOT / 'manage.py'), '--db', str(self.db), *args], capture_output=True, text=True, env=self.env, cwd=ROOT, timeout=300)
        if ok:
            assert run.returncode == 0, (args, run.stdout[-800:], run.stderr[-800:])
        return run

    def provision_and_start(self):
        self.manage('installation-identity', '--mode', 'unconfigured', '--reason', 'New commercial customer installation (fictional pilot test)')
        c = self.cfg
        self.manage('company-profile', '--name', c['name'], '--phone', c['phone'], '--email', c['email'], '--timezone', c['timezone'])
        out = self.manage('setup-code').stdout.splitlines()
        self.setup_code = out[1].strip(); self.env['CROWNOPS_SETUP_CODE_HASH'] = out[3].strip().split('=', 1)[1]
        self.port = free_port(); self.url = f'http://127.0.0.1:{self.port}'
        self.server = subprocess.Popen([sys.executable, str(ROOT / 'manage.py'), '--db', str(self.db), 'serve', '--host', '127.0.0.1', '--port', str(self.port)],
                                       env=self.env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(300):
            try:
                if httpx.get(self.url + '/api/health', timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError(self.key + ' did not start')

    def client(self, email):
        client = httpx.Client(base_url=self.url, timeout=30)
        r = client.post('/api/login', json={'email': email, 'password': PASSWORD}); assert r.status_code == 200, r.text
        client.headers['x-csrf-token'] = r.json()['csrf']; return client


def ok(r):
    assert r.status_code == 200, (r.request.method, str(r.request.url), r.status_code, r.text[:400])
    return r.json()


class DocumentedProvisioningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); base = Path(cls.temp.name)
        cls.inst = {k: Installation(k, base) for k in COMPANIES}
        cls.results = {}
        try:
            for inst in cls.inst.values():
                inst.provision_and_start()
                cls.results[inst.key] = cls.lifecycle(inst)
        except Exception:
            cls.stop(); raise

    @classmethod
    def stop(cls):
        for inst in cls.inst.values():
            if getattr(inst, 'server', None):
                inst.server.terminate(); inst.server.wait(timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.stop(); cls.temp.cleanup()

    @classmethod
    def lifecycle(cls, inst):
        c = inst.cfg; fr = c['language'] == 'French'; result = {}
        public = httpx.Client(base_url=inst.url, timeout=30)
        result['wrong_code'] = public.post('/api/setup-owner', json={'setup_code': 'guess', 'name': 'Owner', 'email': c['owner'], 'password': PASSWORD, 'confirm_password': PASSWORD}).status_code
        ok(public.post('/api/setup-owner', json={'setup_code': inst.setup_code, 'name': 'Fictional Owner', 'email': c['owner'], 'password': PASSWORD, 'confirm_password': PASSWORD}))
        owner = inst.client(c['owner'])
        # Owner configuration through the documented settings APIs (the Company profile screen uses the same calls)
        ok(owner.put('/api/company/pricing-policy', json={'version': 0, 'approved': True, 'truck_minimums': [[6, 3]], 'default_minimum_hours': '3',
            'moving_packing_minimum_hours': '3', 'small_afternoon_minimum_hours': '2', 'local_fuel_per_truck': c['fuel'], 'extra_truck_fee': '25',
            'long_distance_km_rate': '1', 'long_distance_km_buffer': '1.05', 'long_distance_transport_multiplier': '1.1', 'junk_extra_labour': '60',
            'default_deposit': '200', 'limited_liability_text': 'Released value per signed agreement', 'junk_tiers': JUNK}))
        ok(owner.post('/api/annual-rates', json={'label': 'All seasons', 'start_date': '2026-01-01', 'end_date': '2027-12-31', 'note': 'Owner approved',
            **{f'rate_{n}': c['rate'] + 30 * (n - 2) for n in range(2, 7)}}))
        ok(owner.post('/api/platform/settings/billing_policy', json={'approved': True, 'review_note': 'Owner reviewed', 'terms': {
            'issuer': {'name': c['name'], 'address': '1 Fictional Way', 'email': c['email'], 'phone': c['phone']}, 'taxes': c['tax'], 'catalogue': [],
            'hourly_rate': 1, 'minimum_hours': 0, 'transport_amount': 0, 'fuel_amount': 0, 'discount_amount': 0, 'approval_reference': 'Owner approved'}}))
        ok(owner.post('/api/platform/settings/client_terms', json={'approved': True, 'version': 'v1', 'review_note': 'Owner reviewed',
            'text': 'Fictional booking terms written by ' + c['name'] + ' for this pilot validation only.'}))
        key = 'moving-fr' if fr else 'moving-en'
        tpl = next(t for t in ok(owner.get('/api/crm/templates/manage')) if t['template_key'] == key)
        ok(owner.put('/api/crm/templates/' + key, json={'title': tpl['body']['title'], 'liability': 'Owner-written fictional liability terms.', 'fragile': '', 'approved': True, 'updated_at': tpl['updated_at']}))
        png = io.BytesIO(); Image.new('RGB', (60, 30), 'teal').save(png, 'PNG')
        ok(owner.post('/api/company/logo', files={'file': ('logo.png', png.getvalue(), 'image/png')}))
        result['setup'] = ok(owner.get('/api/company/setup-status'))
        # Staff, crew and truck
        invite = ok(owner.post('/api/invitations', json={'email': c['sales'], 'name': 'Fictional Sales', 'role': 'sales', 'send_email': False}))
        ok(public.post('/api/accept-invitation', json={'token': urlsplit(invite['url']).fragment.removeprefix('invite='), 'password': PASSWORD, 'confirm_password': PASSWORD}))
        sales = inst.client(c['sales'])
        result['sales_cannot_change_pricing'] = sales.put('/api/company/pricing-policy', json={'version': 1}).status_code
        crew = [ok(owner.post('/api/employees', json={'name': f'Fictional Mover {n}', 'role': 'Mover', 'hourly_rate': 25}))['id'] for n in (1, 2)]
        truck = ok(owner.post('/api/vehicles', json={'name': 'Fictional Truck', 'color_hex': '#3366AA'}))['id']
        ok(owner.post('/api/employees/' + crew[0] + '/verify-driver', json={'verified': True, 'note': 'Fictional licence and authorization reviewed'}))
        activated = owner.post('/api/vehicles/' + truck + '/activate', json={'note': 'Fictional fleet review'})
        result['truck_activation'] = activated.status_code
        # Inquiry from the website form, estimate, client acceptance, deposit
        ok(public.post('/api/request', json={'request_id': 'web-' + inst.key + '-0001', 'permission_to_contact': True,
            'lead': {'client_name': 'Fictional Client ' + inst.key, 'email': inst.key + '.client@example.invalid', 'phone': '5555550100', 'move_date': '2026-11-20', 'language': c['language'],
                     'pickup': '1 Fictional St', 'dropoff': '2 Fictional Ave'}}))
        lead = next(l for l in ok(sales.get('/api/leads', params={'q': 'Fictional Client ' + inst.key}))['items'] if l['client_name'] == 'Fictional Client ' + inst.key)
        quote = ok(sales.post('/api/crm/quotes', json=dict(lead_id=lead['id'], lead_version=lead['version'], service_type='Moving', language=c['language'], template_key=key,
            move_date='2026-11-20', movers=2, trucks=1, hours_low=3, hours_high=4, minimum_hours=3, transport_hours=1, start_window='morning',
            quote_title='Local move', draft_key='q-' + inst.key, service_details=dict(pickup='1 Fictional St', dropoff='2 Fictional Ave', inventory='Boxes'))))
        final = ok(sales.post('/api/crm/quotes/' + quote['id'] + '/finalize', json={'send_now': False, 'sync_quickbooks': False}))
        portal = httpx.Client(base_url=inst.url, timeout=30)
        exch = ok(portal.post('/api/portal/exchange', json={'token': urlsplit(final['portal_url']).fragment.removeprefix('access=')}))
        portal.headers['x-csrf-token'] = exch['csrf']
        ok(portal.post('/api/portal/quotes/' + quote['id'] + '/accept', json={'agree': True, 'signed_name': 'Fictional Client', 'terms_version': 'v1'}))
        result['portal_html'] = portal.get('/portal').text
        deposit = ok(sales.post('/api/crm/quotes/' + quote['id'] + '/manual-deposit', json={'method': 'Cash', 'mode': 'received', 'amount': 200, 'reference': 'Fictional deposit'}))
        jid = deposit['job_id']
        # Dispatch: crew and truck through the planning API, then start and complete
        job = next(j for j in ok(owner.get('/api/jobs')) if j['id'] == jid)
        zone = ZoneInfo(c['timezone'])
        local = lambda v: datetime.fromisoformat(v.replace('Z', '+00:00')).astimezone(zone).strftime('%Y-%m-%dT%H:%M')
        result['local_start'] = local(job['start_at'])
        ok(owner.put('/api/jobs/' + jid + '/plan', json={'version': job['version'], 'start_at': local(job['start_at']), 'end_at': local(job['end_at']),
            'crew_ids': crew, 'truck_ids': [truck], 'capacity_reviewed': True, 'reason': 'Assign fictional crew and truck'}))
        for status_name in ('In progress', 'Completed'):
            job = next(j for j in ok(owner.get('/api/jobs')) if j['id'] == jid)
            ok(owner.patch('/api/jobs/' + jid, json={'version': job['version'], 'status': status_name}))
        iid = ok(owner.post('/api/jobs/' + jid + '/invoice-from-quote'))['id']
        inv = ok(owner.get('/api/invoices/' + iid))
        inv = ok(owner.patch('/api/invoices/' + iid, json={'version': inv['version'], 'service_hours': 3.5, 'quantities': {}, 'notes': '', 'reason': 'Actuals checked'}))
        ok(owner.post('/api/invoices/' + iid + '/issue', json={'version': inv['version'], 'confirmed': True}))
        issued = ok(owner.get('/api/invoices/' + iid))
        balance = issued['calculation']['balance_cents'] if 'balance_cents' in issued['calculation'] else issued['calculation']['total_cents']
        collected = ok(owner.post('/api/invoices/' + iid + '/collect', json={'amount': balance / 100, 'method': 'Cash', 'reference': 'Fictional cash',
                                                                             'request_id': uuid.uuid4().hex}))
        result['collected_unverified'] = collected.get('verified')
        ok(owner.post('/api/collections/' + collected['id'] + '/verify', json={'evidence': 'Fictional cash handover counted by owner'}))
        result['invoice'] = ok(owner.get('/api/invoices/' + iid))
        result['quote'] = quote
        result['messages'] = ok(owner.get('/api/outbox')) if owner.get('/api/outbox').status_code == 200 else []
        result['owner_token'] = owner.cookies.get('crownops_session')
        # Operations through the documented commands
        result['backup'] = json.loads(inst.manage('backup-run', '--keep', '3').stdout)
        result['db_status'] = inst.manage('db-status').returncode
        result['ops'] = json.loads(inst.manage('ops-check').stdout)
        return result

    def test_owner_setup_requires_this_installations_code_and_completes_configuration(self):
        for key, r in self.results.items():
            with self.subTest(company=key):
                self.assertEqual(r['wrong_code'], 401)
                states = {i['key']: i['state'] for i in r['setup']['items']}
                self.assertTrue(r['setup']['ready'], states); self.assertEqual(states['logo'], 'approved')
                self.assertEqual(r['sales_cannot_change_pricing'], 403); self.assertEqual(r['truck_activation'], 200)

    def test_full_lifecycle_prices_taxes_and_payment_verification(self):
        for key, r in self.results.items():
            c = COMPANIES[key]
            with self.subTest(company=key):
                self.assertEqual(r['quote']['result']['low_cents'], (c['rate'] * 3 + c['rate'] * 1 + int(c['fuel'])) * 100)
                calc = r['invoice']['calculation']
                subtotal = c['rate'] * 350 + c['rate'] * 100 + int(c['fuel']) * 100
                from decimal import Decimal, ROUND_HALF_UP  # the documented invoice rule (Python's round() is half-even)
                tax = int((Decimal(subtotal) * Decimal(str(c['tax'][0]['rate_percent'])) / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                self.assertEqual(calc['total_cents'], subtotal + tax)
                self.assertEqual(r['invoice']['status'], 'Issued'); self.assertFalse(r['collected_unverified'])
                self.assertEqual(r['invoice']['terms']['issuer']['name'], c['name'])
                self.assertEqual(r['local_start'], '2026-11-20T09:00')

    def test_operations_backup_schema_and_monitoring_are_healthy(self):
        for key, r in self.results.items():
            with self.subTest(company=key):
                self.assertEqual(r['backup']['state'], 'ok'); self.assertTrue(r['backup']['restore_verified']); self.assertEqual(r['backup']['upload_files'], 0)
                self.assertEqual(r['db_status'], 0)
                self.assertEqual(r['ops']['state'], 'ok', r['ops']['findings'])

    def test_installations_are_isolated_and_branded_independently(self):
        a, b = self.inst['north'], self.inst['coast']
        for mine, other in ((a, b), (b, a)):
            with self.subTest(company=mine.key):
                stranger = httpx.Client(base_url=mine.url, cookies={'crownops_session': self.results[other.key]['owner_token']})
                self.assertEqual(stranger.get('/api/me').status_code, 401)
                html = self.results[mine.key]['portal_html']
                self.assertNotIn(other.cfg['name'], html); self.assertNotIn('Crown Movers', html)
                self.assertEqual(httpx.get(mine.url + '/api/company/public-profile').json()['name'], mine.cfg['name'])
                self.assertIn(mine.cfg['timezone'], httpx.get(mine.url + '/business-zone.js').text)
        self.assertNotEqual(a.env['CROWNOPS_SECRET_KEY'], b.env['CROWNOPS_SECRET_KEY'])
        self.assertNotEqual(a.db.resolve(), b.db.resolve())


if __name__ == '__main__':
    unittest.main()
