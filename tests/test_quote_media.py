"""Editable quote documents and actual media conversion, using fictional client data."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import copy,json,os,subprocess,tempfile,unittest
from PIL import Image
from pillow_heif import register_heif_opener
from app.store import connect,transaction,one,insert,now,dumps
from app.platform_common import put_setting
import test_template_quotes as templates
import test_move_photos as photos

class QuoteEditorTests(unittest.TestCase):
    def setUp(self):
        self.f=templates.TemplateQuoteTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_custom_material_catalogue_override_zero_quantity_and_billing_survive_revision(self):
        data=copy.deepcopy(self.f.data)
        data.update(materials=[{'code':'TV_PROTECTION_PACKAGE','name':'TV protection — agreed price','qty':2,'unit_price':25,'price_override':True,'note':'Agreed by Sales'},
          {'code':'CUSTOM_BAGS','name':'Mattress bag','qty':3,'unit_price':14.99,'price_override':True},
          {'code':'OPTIONAL_WRAP','name':'Optional bubble wrap','qty':0,'unit_price':39.99,'price_override':True}],
          billing={'name':'Fictional Billing Contact','company':'Fictional Business','address':'100 Billing Test Road','email':'billing@example.invalid','phone':'5145550111'},
          expiry_date='2026-12-01',payment_terms='Due on completion',transport_mode='local_amount',fixed_transport=35,local_fuel_rate=0,
          service_line_name='Packing and moving service',service_line_note='Three to four hours, reviewed scope',media_link='https://drive.google.com/drive/folders/fictional-test')
        first=self.f.save(data);self.assertEqual(first.status_code,200,first.text);q=first.json()
        self.assertEqual(q['result']['materials_cents'],9497);self.assertEqual(q['result']['transport_cents'],3500);self.assertEqual(q['result']['fuel_cents'],0)
        self.assertEqual(q['inputs']['billing']['company'],'Fictional Business');self.assertEqual(q['result']['line_items'][0]['name'],'Packing and moving service')
        optional=next(x for x in q['result']['line_items'] if x.get('code')=='OPTIONAL_WRAP');self.assertEqual(optional['total_cents'],0)
        links=self.f.client.get('/api/leads/'+q['lead_id']+'/media-links').json();self.assertEqual(len(links),1)
        # A saved revision freezes the explicitly reviewed unit price even if the reusable list changes.
        with transaction(self.f.path) as db:db.execute("UPDATE material_catalog SET sell_cents=9900 WHERE code='TV_PROTECTION_PACKAGE'")
        data.pop('new_client');data.pop('service_details');data.update(lead_id=q['lead_id'],lead_version=q['lead_version'],draft_key='quote-revision-price')
        again=self.f.save(data);self.assertEqual(again.status_code,200,again.text);self.assertEqual(again.json()['result']['materials_cents'],9497)
        self.assertEqual(len(self.f.client.get('/api/leads/'+q['lead_id']+'/media-links').json()),1)

    def test_bad_materials_billing_links_and_missing_local_amount_roll_back_everything(self):
        for changes in [{'materials':[{'name':'Test','qty':-1,'unit_price':1}]},{'materials':[{'name':'Test','qty':1.5,'unit_price':1}]},
                        {'materials':[{'name':'Test','qty':1,'unit_price':'NaN'}]}, {'billing':{'email':'invalid'}},{'media_link':'javascript:alert(1)'},
                        {'expiry_date':'2026-02-30'},{'transport_mode':'local_amount','fixed_transport':None},{'movers':2.5}]:
            data=copy.deepcopy(self.f.data);data.update(changes)
            response=self.f.save(data);self.assertEqual(response.status_code,422,(changes,response.text));self.assertEqual(self.f.count('quotes'),0);self.assertEqual(self.f.count('leads'),0)

    def test_material_prices_and_billing_copy_to_invoice_preparation(self):
        data=copy.deepcopy(self.f.data);data.update(materials=[{'code':'CUSTOM_BAG','name':'Mattress bag','qty':2,'unit_price':14.99,'price_override':True}],billing={'company':'Fictional Business','address':'Billing Test Address','email':'billing@example.invalid'})
        quote=self.f.save(data).json()
        policy={'issuer':{'name':'Fictional Crown','address':'Test company address'},'taxes':[],'catalogue':[],'approval_reference':'Fictional test policy'}
        with transaction(self.f.path) as db:
            db.execute("UPDATE quotes SET status='Accepted',approved_by='sales',approved_at=? WHERE id=?",(now(),quote['id']))
            insert(db,'jobs',dict(id='JTEST',lead_id=quote['lead_id'],quote_id=quote['id'],title='Fictional job',phase='Moving',start_at='2026-11-15T14:00:00+00:00',end_at='2026-11-15T20:00:00+00:00',created_by='sales',created_at=now()))
            put_setting(db,'billing_policy',{'approved':True,'terms':policy})
        result=self.f.client.post('/api/jobs/JTEST/invoice-from-quote',json={});self.assertEqual(result.status_code,200,result.text)
        with closing(connect(self.f.path)) as db:inv=one(db,'SELECT * FROM job_invoices')
        terms=json.loads(inv['terms_json']);actual=json.loads(inv['actual_json'])
        self.assertEqual(terms['customer']['name'],'Fictional Business');self.assertEqual(terms['customer']['address'],'Billing Test Address')
        self.assertEqual(terms['catalogue'][0]['unit_cents'],1499);self.assertEqual(actual['quantities']['CUSTOM_BAG'],2)

    def test_job_quick_revision_preserves_local_fee_materials_billing_and_original_snapshot(self):
        data=copy.deepcopy(self.f.data)
        data.update(transport_mode='local_amount',fixed_transport=35,local_fuel_rate=0,payment_terms='Due at completion',
          materials=[{'code':'BAG','name':'Mattress bag','qty':2,'unit_price':15,'price_override':True}],
          billing={'company':'Fictional Billing','email':'payer@example.invalid','address':'Billing only address'})
        original=self.f.save(data).json()
        with transaction(self.f.path) as db:
            insert(db,'jobs',dict(id='QUICK',lead_id=original['lead_id'],quote_id=original['id'],title='Fictional quick review',phase='Moving',start_at='2026-11-15T14:00:00+00:00',end_at='2026-11-15T20:00:00+00:00',created_by='sales',created_at=now()))
            before=one(db,'SELECT * FROM quotes WHERE id=?',(original['id'],))
        response=self.f.client.post('/api/jobs/QUICK/quote-revision',json={'hours_low':4,'hours_high':5,'fixed_transport':45})
        self.assertEqual(response.status_code,200,response.text);revised=response.json()
        self.assertEqual(revised['result']['transport_cents'],4500);self.assertEqual(revised['result']['materials_cents'],3000)
        self.assertEqual(revised['result']['low_cents'],61500);self.assertEqual(revised['inputs']['billing']['company'],'Fictional Billing')
        self.assertEqual(revised['inputs']['payment_terms'],'Due at completion')
        with closing(connect(self.f.path)) as db:self.assertEqual(one(db,'SELECT * FROM quotes WHERE id=?',(original['id'],)),before)

    def test_junk_quote_can_also_include_materials_and_approved_extras(self):
        response=self.f.client.post('/api/crm/quotes/calculate',json={'service_type':'Junk Removal','junk_tier':'single','materials':[{'code':'BAG','name':'Disposal bag','qty':2,'unit_price':5}],'other_amount':10,'discount_amount':5})
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['low_cents'],19000)

class MediaTests(unittest.TestCase):
    def setUp(self):
        self.f=photos.MovePhotoTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def test_48_megapixel_camera_jpeg_and_heic_are_resized_and_private(self):
        register_heif_opener()
        for size,fmt,name in [((8000,6000),'JPEG','IMG_camera.jpg'),((200,150),'HEIF','IMG_phone.heic')]:
            stream=BytesIO();Image.new('RGB',size,'#23466b').save(stream,format=fmt)
            response=self.f.upload(stream.getvalue(),name);self.assertEqual(response.status_code,200,response.text)
            r=response.json();self.assertEqual(r['scan_status'],'Photo checked');self.assertEqual(self.f.other.get('/api/uploads/'+r['id']).status_code,403)
            image=Image.open(BytesIO(self.f.crew.get('/api/uploads/'+r['id']).content));self.assertLessEqual(max(image.size),2048);self.assertFalse(image.getexif())

    def test_mov_walkthrough_is_processed_and_invalid_video_is_not_saved(self):
        source=self.f.root/'clip.mov'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=320x240:d=1','-metadata','title=PRIVATE TEST','-c:v','libx264','-threads','1','-y',str(source)],check=True)
        r=self.f.upload(source.read_bytes(),'clip.mov');self.assertEqual(r.status_code,200,r.text);item=r.json();self.assertEqual(item['scan_status'],'Video checked');self.assertEqual(item['mime'],'video/mp4')
        ready=self.f.crew.get('/api/uploads/'+item['id']);self.assertEqual(ready.status_code,200);self.assertIn('inline',ready.headers['content-disposition']);self.assertNotIn(b'PRIVATE TEST',ready.content)
        count=self.f.count();bad=self.f.upload(b'\x00\x00\x00\x18ftypqt  '+b'bad'*20,'bad.mov');self.assertEqual(bad.status_code,422,bad.text);self.assertEqual(self.f.count(),count)

    def test_retry_of_saved_photo_succeeds_even_when_quota_is_full(self):
        first=self.f.upload();self.assertEqual(first.status_code,200,first.text)
        with patch.dict(os.environ,{'CROWNOPS_PHOTO_STORAGE_MB':'0','CROWNOPS_PROJECT_UPLOAD_MB':'0'}):
            retry=self.f.upload();self.assertEqual(retry.status_code,200,retry.text)
            self.assertEqual(first.json()['id'],retry.json()['id'])
        self.assertEqual(self.f.count(),1)

    def test_unsafe_legacy_folder_link_is_never_presented_to_clients_or_crew(self):
        with transaction(self.f.path) as db:db.execute("UPDATE leads SET photo_link='javascript:alert(1)' WHERE id='L1'")
        self.assertEqual(self.f.crew.get('/api/leads/L1/media-links').json(),[])
        self.assertEqual(self.f.portal.get('/api/portal/project').json()['media_links'],[])

    def test_media_links_are_shared_only_with_authorized_project_and_job_team(self):
        data={'url':'https://drive.google.com/drive/folders/fictional','label':'Client walkthrough'}
        result=self.f.portal.post('/api/portal/media-links',json=data);self.assertEqual(result.status_code,200,result.text)
        self.assertTrue(self.f.portal.post('/api/portal/media-links',json=data).json()['duplicate'])
        self.assertEqual(len(self.f.crew.get('/api/leads/L1/media-links').json()),1);self.assertEqual(self.f.other.get('/api/leads/L1/media-links').status_code,403)
        self.assertEqual(self.f.portal.get('/api/portal/project').json()['media_links'][0]['label'],'Client walkthrough')
        self.assertEqual(self.f.portal.post('/api/portal/media-links',json={'url':'javascript:alert(1)'}).status_code,422)
        self.assertEqual(self.f.crew.post('/api/leads/L2/media-links',json={**data,'job_id':'J2'}).status_code,403)
        with transaction(self.f.path) as db:db.execute("DELETE FROM assignments WHERE job_id='J1'")
        self.assertEqual(self.f.crew.get('/api/leads/L1/media-links').status_code,403)

if __name__=='__main__':unittest.main()
