"""COM-8 settings backend and COM-2c white-label delivery. Fictional data only."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app.business_zone import DEFAULT_ZONE, set_business_zone
from app.main import create_app, sha
from app.platform_common import put_setting
from app.store import connect, insert, now, transaction

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'app' / 'static'
CROWN = re.compile(r'Crown Movers|CROWN MOVERS|crownmovers|514-606-4030|606-4030|5146064030|\bCrown\b(?!Ops)')
TEXT_FILES = sorted(p for p in STATIC.glob('*') if p.suffix in ('.js', '.css', '.html', '.webmanifest'))
COMPANY = {'name': "O'Neil Moving & Storage", 'short_name': "O'Neil", 'phone': '(902) 555-0147', 'email': 'office@oneil.example.invalid',
           'timezone': 'America/Halifax', 'brand_color': '#225588'}


def png(size=(64, 40), color='red', trailing=b''):
    out = BytesIO(); Image.new('RGB', size, color).save(out, 'PNG'); return out.getvalue() + trailing


class Installation(unittest.TestCase):
    MODE = None

    def setUp(self):
        env = patch.dict(os.environ, {'CROWNOPS_ENV': 'test', 'CROWNOPS_ALLOW_EXTERNAL': '0', 'QBO_ENV': 'sandbox'})
        env.start(); self.addCleanup(env.stop); self.addCleanup(set_business_zone, DEFAULT_ZONE)
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'wl.sqlite3'
        if self.MODE:
            from app.store import initialize
            initialize(self.path)
            with transaction(self.path) as db:
                put_setting(db, 'company_identity_mode', self.MODE)
        self.app = create_app(self.path)
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.c = {}
        with transaction(self.path) as db:
            for role in ('owner', 'sales'):
                insert(db, 'users', dict(id=role, email=role + '@example.invalid', name=role, role=role, password_hash='x', active=1, created_at=now()))
                insert(db, 'sessions', dict(token_hash=sha(role + '-s'), user_id=role, csrf=role + '-c', expires_at=expires))
        for role in ('owner', 'sales'):
            client = TestClient(self.app); client.cookies.set('crownops_session', role + '-s'); client.headers['x-csrf-token'] = role + '-c'
            self.c[role] = client; self.addCleanup(client.close)
        self.public = TestClient(self.app); self.addCleanup(self.public.close)

    def configure(self, **changes):
        version = self.c['owner'].get('/api/company/profile').json()['version']
        r = self.c['owner'].put('/api/company/profile', json={**COMPANY, **changes, 'version': version})
        self.assertEqual(r.status_code, 200, r.text); return r.json()


class LegacyCrownUnchangedTests(Installation):
    def test_every_static_text_asset_and_icon_is_byte_identical(self):
        for p in TEXT_FILES + sorted((STATIC / 'icons').glob('*.png')) + [STATIC / 'icon.svg']:
            rel = p.relative_to(STATIC).as_posix()
            with self.subTest(asset=rel):
                r = self.public.get('/static/' + rel)
                self.assertEqual(r.status_code, 200); self.assertEqual(r.content, p.read_bytes())
        self.assertEqual(self.public.get('/manifest.webmanifest').content, (STATIC / 'manifest.webmanifest').read_bytes())
        self.assertEqual(self.public.get('/').text, self.app.state.ui_index)


class ConfiguredCompanyWhiteLabelTests(Installation):
    MODE = 'unconfigured'

    def test_all_text_assets_carry_the_company_identity_and_still_parse(self):
        self.configure()
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node.js is required to verify rewritten scripts')
        with tempfile.TemporaryDirectory() as td:
            for p in TEXT_FILES:
                rel = p.name
                with self.subTest(asset=rel):
                    r = self.public.get('/static/' + rel)
                    self.assertEqual(r.status_code, 200)
                    self.assertIsNone(CROWN.search(r.text), rel)
                    self.assertNotIn('etag', r.headers); self.assertEqual(r.headers['cache-control'], 'no-cache')
                    if rel.endswith('.js'):
                        target = Path(td) / rel; target.write_text(r.text)
                        check = subprocess.run([node, '--check', str(target)], capture_output=True, text=True)
                        self.assertEqual(check.returncode, 0, rel + ': ' + check.stderr[-400:])
        client_js = self.public.get('/static/client.js').text
        self.assertIn("O\\u0027Neil", client_js); self.assertIn('\\u0028902\\u0029 555-0147', self.public.get('/static/request.js').text)  # JS-encoded phone
        manifest = json.loads(self.public.get('/manifest.webmanifest').text)
        self.assertIn("O'Neil Moving & Storage", manifest['name'])
        for page in ('/', '/portal', '/request', '/approval', '/participate', '/invoice-access'):
            with self.subTest(page=page):
                html = self.public.get(page).text
                self.assertIsNone(CROWN.search(html), page)

    def test_conditional_requests_never_receive_a_stale_crown_asset(self):
        self.configure()
        r = self.public.get('/static/client.js', headers={'If-None-Match': '"anything"', 'If-Modified-Since': 'Wed, 21 Oct 2099 07:28:00 GMT'})
        self.assertEqual(r.status_code, 200); self.assertIsNone(CROWN.search(r.text))

    def test_icons_are_generated_from_initials_then_logo(self):
        self.configure()
        icon = Image.open(BytesIO(self.public.get('/static/icons/app-192.png').content))
        self.assertEqual(icon.size, (192, 192))
        self.assertNotEqual(self.public.get('/static/icons/app-192.png').content, (STATIC / 'icons' / 'app-192.png').read_bytes())
        before = self.public.get('/static/icons/app-512.png').content
        self.assertEqual(self.c['owner'].post('/api/company/logo', files={'file': ('logo.png', png(), 'image/png')}).status_code, 200)
        self.assertNotEqual(self.public.get('/static/icons/app-512.png').content, before)
        svg = self.public.get('/static/icon.svg')
        self.assertEqual(svg.headers['content-type'], 'image/svg+xml'); self.assertIn('data:image/png;base64,', svg.text)
        self.assertIsNone(CROWN.search(svg.text))

    def test_unconfigured_installation_shows_no_crown_identity(self):
        for asset in ('/static/client.js', '/static/request.html', '/manifest.webmanifest', '/'):
            with self.subTest(asset=asset):
                self.assertIsNone(CROWN.search(self.public.get(asset).text), asset)

    def test_adversarial_names_are_encoded_inert_in_every_context(self):
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node.js is required')
        from app.branding import _escape
        hostile = ["Ann's \"Best\" Movers", 'Evil `x` ${globalThis.pwned=1} \\ Movers', 'Déménagement Élite 🚚', 'Movers */ --> // \u0027', "'+alert(1)+'"]
        for name in hostile:
            with self.subTest(name=name):
                e = _escape(name, 'js')
                probe = (f"const a='{e}',b=\"{e}\",c=`{e}`,r=/{e}/;"
                         "process.stdout.write(JSON.stringify({a,b,c,r:r.source,pwned:globalThis.pwned===1}))")
                out = subprocess.run([node, '-e', probe], capture_output=True, text=True)
                self.assertEqual(out.returncode, 0, out.stderr)
                got = json.loads(out.stdout)
                self.assertEqual((got['a'], got['b'], got['c'], got['pwned']), (name, name, name, False))
                self.assertNotIn('*/', _escape(name, 'css')); self.assertEqual(json.loads('"' + _escape(name, 'json') + '"'), name)

    def test_hostile_profile_keeps_every_asset_valid_and_product_references_intact(self):
        node = shutil.which('node'); self.assertIsNotNone(node, 'Node.js is required')
        name = 'Evil `x` ${alert(1)} \\ "Q" Ünïcode 🚚 */ Movers'
        self.configure(name=name, short_name="O'Brien `")
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for p in TEXT_FILES:
                body = self.public.get('/static/' + p.name).text; original = p.read_text()
                with self.subTest(asset=p.name):
                    self.assertEqual(body.count('CrownOps'), original.count('CrownOps'))
                    self.assertIsNone(CROWN.search(body))
                    if p.suffix == '.html':
                        self.assertEqual((body.count('<'), body.count('>')), (original.count('<'), original.count('>')))
                    if p.suffix == '.css':
                        self.assertEqual(body.count('*/'), original.count('*/'))
                if p.suffix == '.js':
                    target = Path(td) / p.name; target.write_text(body); paths.append(str(target))
            check = ("const vm=require('vm'),fs=require('fs');const bad=[];for(const f of process.argv.slice(1)){"
                     "try{new vm.Script(fs.readFileSync(f,'utf8'),{filename:f});}catch(e){bad.push(f+': '+e.message);}}"
                     "process.stdout.write(JSON.stringify(bad));")
            out = subprocess.run([node, '-e', check, *paths], capture_output=True, text=True)
            self.assertEqual(json.loads(out.stdout), [], out.stderr)
        manifest = json.loads(self.public.get('/manifest.webmanifest').text)
        self.assertIn(name, manifest['name'])
        sources = {'/': self.app.state.ui_index, '/portal': (STATIC / 'portal.html').read_text(), '/invoice-access': (STATIC / 'invoice_access.html').read_text()}
        for page, source in sources.items():
            body = self.public.get(page).text  # name is HTML text: inert, and adds no elements or scripts
            self.assertEqual((body.count('<'), body.count('<script')), (source.count('<'), source.count('<script')), page)

    def test_branding_change_is_never_served_stale(self):
        self.configure(name='First Fictional Movers')
        self.assertIn('First Fictional Movers', self.public.get('/static/client.js').text)
        self.configure(name='Second Fictional Movers')
        body = self.public.get('/static/client.js').text
        self.assertIn('Second Fictional Movers', body); self.assertNotIn('First Fictional Movers', body)


class LogoUploadTests(Installation):
    MODE = 'unconfigured'

    def test_logo_upload_is_manager_only_validated_reencoded_and_audited(self):
        self.assertEqual(self.c['sales'].post('/api/company/logo', files={'file': ('l.png', png(), 'image/png')}).status_code, 403)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertEqual(self.c['owner'].post('/api/company/logo', files={'file': ('l.svg', svg, 'image/svg+xml')}).status_code, 422)
        self.assertEqual(self.c['owner'].post('/api/company/logo', files={'file': ('big.png', b'x' * 2_000_100, 'image/png')}).status_code, 422)
        self.assertEqual(self.c['owner'].post('/api/company/logo', files={'file': ('huge.png', png((6100, 10)), 'image/png')}).status_code, 422)
        smuggled = png(trailing=b'<script>alert(1)</script>')
        r = self.c['owner'].post('/api/company/logo', files={'file': ('l.png', smuggled, 'image/png')})
        self.assertEqual(r.status_code, 200, r.text)
        served = self.public.get('/api/company/logo')
        self.assertEqual(served.headers['x-content-type-options'], 'nosniff'); self.assertNotIn(b'<script>', served.content)
        self.assertEqual(Image.open(BytesIO(served.content)).format, 'PNG')
        self.assertEqual(self.c['sales'].delete('/api/company/logo').status_code, 403)
        self.assertEqual(self.c['owner'].delete('/api/company/logo').status_code, 200)
        self.assertEqual(self.public.get('/api/company/logo').status_code, 404)
        with closing(connect(self.path)) as db:
            actions = [r[0] for r in db.execute("SELECT action FROM audit WHERE action LIKE 'Company logo%' ORDER BY id")]
        self.assertEqual(actions, ['Company logo updated', 'Company logo removed'])


class SetupStatusTests(Installation):
    MODE = 'unconfigured'
    POLICY = {'truck_minimums': [[6, 3]], 'default_minimum_hours': '2', 'moving_packing_minimum_hours': '2', 'small_afternoon_minimum_hours': '1',
              'local_fuel_per_truck': '40', 'extra_truck_fee': '30', 'long_distance_km_rate': '1', 'long_distance_km_buffer': '1.05',
              'long_distance_transport_multiplier': '1.1', 'junk_extra_labour': '60', 'default_deposit': '150', 'limited_liability_text': 'Per agreed terms',
              'junk_tiers': {k: [k.title(), '100'] for k in ('single', 'quarter', 'half', 'three_quarter', 'full')}}

    def states(self):
        return {i['key']: i['state'] for i in self.c['sales'].get('/api/company/setup-status').json()['items']}

    def test_checklist_moves_from_incomplete_to_pending_to_approved(self):
        self.assertEqual(self.states(), {'identity': 'incomplete', 'logo': 'optional', 'pricing': 'incomplete', 'rates': 'incomplete', 'billing': 'incomplete',
                                         'templates': 'incomplete', 'terms': 'incomplete'})
        self.configure()
        draft = self.c['owner'].put('/api/company/pricing-policy', json={**self.POLICY, 'approved': False, 'version': 0})
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(self.states()['pricing'], 'pending approval')
        from app.sales_crm_v6 import calculate
        from fastapi import HTTPException
        with closing(connect(self.path)) as db, self.assertRaises(HTTPException):
            calculate(db, {'move_date': '2026-12-01'})  # a draft never prices
        approve = self.c['owner'].put('/api/company/pricing-policy', json={**self.POLICY, 'approved': True, 'version': 1})
        self.assertEqual(approve.status_code, 200, approve.text)
        with transaction(self.path) as db:
            insert(db, 'annual_rate_periods', dict(id='R1', label='All year', start_date='2026-01-01', end_date='2099-12-31', rate_2_cents=12000, rate_3_cents=16000,
                                                   rate_4_cents=20000, rate_5_cents=24000, rate_6_cents=28000, active=1, version=1, note='', approved_by='owner', created_at=now()))
            put_setting(db, 'billing_policy', {'approved': True, 'terms': {'issuer': {'name': COMPANY['name']}}})
            put_setting(db, 'client_terms', {'approved': True, 'version': 't1', 'text': 'Fictional owner-written booking terms.'})
            db.execute("UPDATE crm_quote_templates SET active=1 WHERE template_key='moving-en'")
        status = self.c['sales'].get('/api/company/setup-status').json()
        self.assertTrue(status['ready'], status)
        unapprove = self.c['owner'].put('/api/company/pricing-policy', json={**self.POLICY, 'approved': False, 'version': 2})
        self.assertEqual(unapprove.status_code, 409)  # an approved policy is never replaced by an unapproved draft


class RoundingReconciliationTests(Installation):
    def test_fractional_lines_reconcile_without_changing_the_total(self):
        from decimal import Decimal, ROUND_HALF_UP
        from app.sales_crm_v6 import calculate
        with transaction(self.path) as db:
            db.execute("""INSERT INTO annual_rate_periods (id,label,start_date,end_date,rate_2_cents,rate_3_cents,rate_4_cents,rate_5_cents,rate_6_cents,active,version,note,approved_by,created_at)
                          VALUES ('RX','x','2026-01-01','2027-12-31',13500,17500,22000,26000,31500,1,1,'','t','2026-01-01')""")
        cases = [({'hourly_rate': 100.01, 'hours_low': 3.33, 'hours_high': 3.33, 'transport_hours': 1.37}, Decimal('100.01') * Decimal('3.33') + Decimal('100.01') * Decimal('1.37') + 35),
                 ({'hourly_rate': 99.99, 'hours_low': 3.75, 'hours_high': 4.5, 'transport_hours': 1.25, 'other_amount': 10.005, 'discount_amount': 5.555},
                  Decimal('99.99') * Decimal('3.75') + Decimal('99.99') * Decimal('1.25') + 35 + Decimal('10.005') - Decimal('5.555'))]
        with closing(connect(self.path)) as db:
            for data, exact in cases:
                with self.subTest(data=data):
                    r = calculate(db, {'move_date': '2026-11-20', **data})
                    self.assertEqual(r['low_cents'], int((exact * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
                    self.assertEqual(sum(l['total_cents'] for l in r['line_items']), r['low_cents'])
                    adjustments = [l for l in r['line_items'] if l['name'] == 'Rounding adjustment']
                    self.assertLessEqual(len(adjustments), 1)
                    if adjustments:
                        self.assertLessEqual(abs(adjustments[0]['total_cents']), len(r['line_items']))

    def test_invoices_total_equals_lines_minus_discount_plus_taxes(self):
        from app.invoices import calculate_invoice
        terms = {'minimum_hours': 3, 'hourly_rate_cents': 14999, 'transport_amount_cents': 13333, 'fuel_amount_cents': 3500, 'other_amount_cents': 1001,
                 'catalogue': [{'code': 'BOX', 'name': 'Box', 'unit_cents': 333}], 'discount_amount_cents': 777,
                 'taxes': [{'name': 'GST', 'rate_percent': 5.0}, {'name': 'QST', 'rate_percent': 9.975}]}
        calc = calculate_invoice(terms, {'service_hours': '3.33', 'quantities': {'BOX': 7}})
        subtotal = sum(l['amount_cents'] for l in calc['lines']) - 777
        self.assertEqual(calc['total_cents'], subtotal + sum(t['amount_cents'] for t in calc['taxes']))


if __name__ == '__main__':
    unittest.main()
