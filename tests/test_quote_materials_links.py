"""Editable quote extras and shared references use disposable records only."""
from contextlib import closing
import copy
import json
import unittest
from fastapi.testclient import TestClient
from app.store import connect,transaction,insert,one,now
from app.platform_common import put_setting
import test_template_quotes as template
import test_move_photos as photos


class QuoteExtrasTests(unittest.TestCase):
    setUp=template.TemplateQuoteTests.setUp
    save=template.TemplateQuoteTests.save
    count=template.TemplateQuoteTests.count

    def calc(self,**updates):
        return self.client.post('/api/crm/quotes/calculate',json={**self.data,**updates})

    def revision(self,quote):
        data=copy.deepcopy(quote['inputs']);data.pop('draft_request_hash',None)
        data['draft_key']='edited-draft';return data

    def test_custom_and_catalogue_material_prices_survive_catalogue_changes_and_new_revisions(self):
        self.data['materials']=[{'code':'TV_PROTECTION_PACKAGE','name':'TV packing','qty':2,'unit_price':30},
            {'code':'CUSTOM_BUBBLE','name':'Bubble wrap roll','qty':1,'unit_price':39.99}]
        saved=self.save();self.assertEqual(saved.status_code,200,saved.text);q=saved.json()
        self.assertEqual(q['result']['materials_cents'],9999);self.assertEqual(q['result']['low_cents'],67499)
        with transaction(self.path) as db:db.execute("UPDATE material_catalog SET sell_cents=9900,active=0 WHERE code='TV_PROTECTION_PACKAGE'")
        second=self.save(self.revision(q));self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['result']['materials_cents'],9999)
        self.assertEqual(second.json()['inputs']['materials'],q['inputs']['materials'])
        self.assertEqual(self.count('outbound_messages'),0)

    def test_local_fuel_is_editable_per_truck_and_fixed_transport_is_separate(self):
        r=self.calc(movers=4,trucks=2,hourly_rate=260,local_fuel_per_truck=42.5)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['fuel_cents'],8500)
        self.assertEqual(r.json()['transport_cents'],28500)
        r=self.calc(local_fuel_per_truck=0,fixed_transport=35)
        self.assertEqual(r.json()['fuel_cents'],0);self.assertEqual(r.json()['transport_cents'],3500)
        self.assertEqual(self.calc().json()['fuel_cents'],3500)

    def test_long_distance_keeps_existing_calculation(self):
        r=self.calc(service_type='Long Distance',transport_hours=12,distance_km=1082,distance_rate=.94,local_fuel_per_truck=999)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['transport_cents'],186300)
        self.assertEqual(r.json()['fuel_cents'],106793);self.assertEqual(r.json()['billable_transport_hours'],13.8)

    def test_junk_quotes_do_not_silently_drop_materials_or_discounts(self):
        r=self.calc(service_type='Junk Removal',junk_tier='single',materials=[{'name':'Protection','qty':2,'unit_price':10}],other_amount=15,discount_amount=5)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['low_cents'],20500)
        self.assertEqual(r.json()['materials_cents'],2000);self.assertEqual(r.json()['discount_cents'],500)

    def test_invalid_materials_and_fuel_are_rejected(self):
        base={'code':'TEST','name':'Test material','qty':1,'unit_price':10}
        for change in ({'qty':-1},{'qty':.5},{'unit_price':-1},{'unit_price':'NaN'},{'unit_price':'bad'},{'name':''},{'code':'bad/code'}):
            with self.subTest(change=change):self.assertEqual(self.calc(materials=[{**base,**change}]).status_code,422)
        for value in ({'name':'not a list'},[base,base],['invalid']):self.assertEqual(self.calc(materials=value).status_code,422)
        for value in (-1,'NaN',True):self.assertEqual(self.calc(local_fuel_per_truck=value).status_code,422)

    def test_photo_link_is_saved_atomically_with_new_and_revised_quotes(self):
        self.data['photo_link']='https://drive.google.com/drive/folders/TEST'
        q=self.save().json();self.assertEqual(q['lead_snapshot']['photo_link'],self.data['photo_link'])
        data=self.revision(q);data['photo_link']='https://example.invalid/new-photos'
        result=self.save(data);self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['lead_snapshot']['photo_link'],data['photo_link'])
        original=self.client.get('/api/crm/quotes/'+q['id']).json()['quote']
        self.assertEqual(original['lead_snapshot']['photo_link'],self.data['photo_link'])
        data['draft_key']='stale';self.assertEqual(self.save(data).status_code,409)

    def test_invalid_photo_link_cannot_create_a_client_or_quote(self):
        for link in ('javascript:alert(1)','http://example.invalid/photos','https://user:password@example.invalid/photos'):
            data={**self.data,'photo_link':link};self.assertEqual(self.save(data).status_code,422)
        self.assertEqual(self.count('leads'),0);self.assertEqual(self.count('quotes'),0)

    def test_photo_hub_link_has_conflict_protection_and_does_not_revise_agreed_scope(self):
        q=self.save().json();url='/api/crm/services/'+q['lead_id']+'/photo-link'
        r=self.client.patch(url,json={'expected_link':'','photo_link':'https://example.invalid/photos'})
        self.assertEqual(r.status_code,200,r.text)
        lead=self.client.get('/api/leads/'+q['lead_id']).json();self.assertEqual(lead['version'],q['lead_version'])
        self.assertEqual(self.client.patch(url,json={'expected_link':'','photo_link':'https://example.invalid/old'}).status_code,409)
        crew=TestClient(self.app);self.addCleanup(crew.close);crew.cookies.set('crownops_session','crew-token');crew.headers['x-csrf-token']='crew-csrf'
        self.assertEqual(crew.patch(url,json={'expected_link':lead['photo_link'],'photo_link':''}).status_code,403)
        self.assertEqual(self.client.patch(url,json={'expected_link':lead['photo_link'],'photo_link':''}).status_code,200)

    def test_accepted_custom_material_prices_are_available_on_the_job_invoice(self):
        self.data['materials']=[{'code':'CUSTOM_BOX','name':'Special box','qty':2,'unit_price':4.99}]
        q=self.save().json()
        with transaction(self.path) as db:
            db.execute("UPDATE quotes SET status='Accepted',approved_by='sales',approved_at=? WHERE id=?",(now(),q['id']))
            insert(db,'jobs',dict(id='JOB',lead_id=q['lead_id'],quote_id=q['id'],title='Test move',phase='Moving',start_at='2026-11-15T14:00:00Z',end_at='2026-11-15T18:00:00Z',created_by='sales',created_at=now()))
            put_setting(db,'billing_policy',{'approved':True,'terms':{'issuer':{'name':'Test company','address':'Test address'},'taxes':[],
                'catalogue':[{'code':'CUSTOM_BOX','name':'Old box price','unit_price':99}], 'approval_reference':'Test reviewed policy'}})
        r=self.client.post('/api/jobs/JOB/invoice-from-quote',json={});self.assertEqual(r.status_code,200,r.text)
        with closing(connect(self.path)) as db:
            terms=json.loads(one(db,'SELECT terms_json FROM job_invoices WHERE id=?',(r.json()['id'],))['terms_json'])
        self.assertEqual(terms['catalogue'],[{'code':'CUSTOM_BOX','name':'Special box','unit_cents':499}])


class SharedPhotoLinkTests(unittest.TestCase):
    setUp=photos.MovePhotoTests.setUp
    staff=photos.MovePhotoTests.staff
    image=photos.MovePhotoTests.image

    def test_saving_a_link_makes_it_visible_to_assigned_crew_only(self):
        link='https://drive.google.com/drive/folders/TEST'
        r=self.owner.patch('/api/crm/services/L1/photo-link',json={'expected_link':'','photo_link':link})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.crew.get('/api/ux/final/job/J1').json()['client'].get('photo_link'),link)
        self.assertNotIn('photo_link',self.other.get('/api/ux/final/job/J1').json()['client'])
