"""Isolated client/mover workflow: no real contacts, reviews, prizes or messages."""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
import json
import os
import subprocess
import unittest
from fastapi.testclient import TestClient
import test_launch_access as fixture
from app.store import connect, transaction, insert, one, now, dumps
from app.platform_common import put_setting, business_day

RULES='Fictional test rules only. Free entry without purchase, review, video or social post. One entry per eligible adult. One $500 prize. Eligibility and skill testing verified before award. Contact the fictional organizer for details.'
MEDIA={'approved':True,'review_note':'Fictional owner approval for isolated test','version':'test-v1','release_text':'Fictional English permission: honest video, optional selected channels, $50 discount disclosed. No review required.', 'release_text_fr':'Permission française fictive : vidéo honnête, canaux choisis, rabais de 50 $ divulgué. Aucun avis requis.', 'channels':['Website','Social posts'],'credit_mode':'after_tax'}

class ClientParticipationTests(unittest.TestCase):
    staff=fixture.LaunchAccessTests.staff
    invoice=fixture.LaunchAccessTests.invoice
    def setUp(self):
        fixture.LaunchAccessTests.setUp(self)
        self.crew=self.invoice()
        self.env=patch.dict(os.environ,{'CROWNOPS_UPLOADS_ENABLED':'1','CROWNOPS_UPLOAD_MODE':'photos','CROWNOPS_PHOTO_STORAGE_MB':'256','CROWNOPS_PROJECT_UPLOAD_MB':'256'})
        self.env.start();self.addCleanup(self.env.stop)
        with transaction(self.path) as db:put_setting(db,'media_program',MEDIA)
        self.public=TestClient(self.app);self.addCleanup(self.public.close)
    def open(self,client=None):
        r=(client or self.crew).post('/api/jobs/J1/participation/open',json={'profile':'main'})
        self.assertEqual(r.status_code,200,r.text)
        token=r.json()['url'].split('#access=')[1]
        x=self.public.post('/api/participation/exchange',json={'token':token});self.assertEqual(x.status_code,200,x.text)
        self.public.headers['x-csrf-token']=self.public.get('/api/participation').json()['csrf']
        return token
    def attachment(self):
        with transaction(self.path) as db:
            insert(db,'attachments',dict(id='VIDEO',lead_id='L1',job_id='J1',category='Testimonial',original_name='video.mp4',stored_name='video.mp4',mime='video/mp4',size=1000,sha256='test',scan_status='Video checked',author='client-participation',created_at=now()))
            insert(db,'attachment_media',dict(attachment_id='VIDEO',duration_seconds=35))
            db.execute("UPDATE service_participation SET attachment_id='VIDEO' WHERE job_id='J1'")
    def consent(self,**changes):
        payload={'version':'test-v1','signed_name':'Fictional Client','channels':['Website'],'agree':True,'adult_and_authorized':True,'language':'fr'};payload.update(changes)
        return self.public.post('/api/participation/consent',json=payload)
    def draw(self):
        start=business_day();draw=(date.fromisoformat(start)+timedelta(days=190)).isoformat()
        r=self.owner.post('/api/participation/draws',json={'rules_reviewed':True,'start_date':start,'draw_date':draw,'rules_en':RULES,'rules_fr':RULES})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()['id']
    def enter(self,did,email='free@example.invalid'):
        d=self.public.get('/api/giveaways/'+did).json()
        return self.public.post('/api/giveaways/'+did+'/enter',json={'name':'Free entrant','email':email,'agree':True,'eligible':True,'rules_hash':d['rules_hash']})
    def test_role_boundaries_one_use_private_session_csrf_and_expiry(self):
        for role in ('lead','accounting','dispatch'):
            self.assertEqual(self.staff(role).get('/api/jobs/J1/participation').status_code,403,role)
        token=self.open()
        self.assertEqual(self.public.get('/api/me').status_code,401)
        data=self.public.get('/api/participation').json();self.assertTrue(data['video_available']);self.assertNotIn('invoices',data)
        self.assertEqual(self.public.post('/api/participation/exchange',json={'token':token}).status_code,401)
        self.public.headers['x-csrf-token']='wrong';self.assertEqual(self.consent().status_code,403)
        with transaction(self.path) as db:db.execute("UPDATE participation_access SET expires_at='2000-01-01'")
        self.assertEqual(self.public.get('/api/participation').status_code,401)
        for role in ('manager','crew','sales'):
            c=self.staff(role)
            self.assertEqual(c.put('/api/participation/settings',json={'profiles':[]}).status_code,403)
            self.assertEqual(c.post('/api/participation/draws',json={}).status_code,403)
            self.assertEqual(c.get('/api/participation/draws/unknown/entries').status_code,403)
            self.assertEqual(c.get('/api/participation/settings').json()['draws'],[])
    def test_video_consent_snapshot_credit_exactly_once_and_no_review_needed(self):
        self.open();self.attachment()
        self.assertEqual(self.consent(version='stale').status_code,409)
        self.assertEqual(self.consent(channels=[]).status_code,422)
        self.assertEqual(self.consent(agree=False).status_code,422)
        r=self.consent();self.assertEqual(r.status_code,200,r.text);rid=r.json()['id']
        self.assertTrue(self.consent().json()['already_recorded'])
        with closing(connect(self.path)) as db:
            release=one(db,'SELECT * FROM media_releases WHERE id=?',(rid,));self.assertEqual(release['release_text'],MEDIA['release_text_fr'])
            self.assertEqual(json.loads(release['channels_json']),['Website'])
        body={'invoice_id':'INV','version':1}
        self.assertEqual(self.staff('lead').post('/api/jobs/J1/participation/credit',json=body).status_code,403)
        r=self.crew.post('/api/jobs/J1/participation/credit',json=body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.crew.post('/api/jobs/J1/participation/credit',json=body).status_code,409)
        self.assertTrue(self.crew.post('/api/jobs/J1/participation/credit',json={**body,'version':2}).json()['already_applied'])
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM project_credits').fetchone()[0],1)
            self.assertEqual(json.loads(one(db,"SELECT terms_json FROM job_invoices WHERE id='INV'")['terms_json'])['testimonial_credit_cents'],5000)
            self.assertEqual(db.execute('SELECT count(*) FROM service_review_records').fetchone()[0],0)
        self.assertTrue(self.public.get('/api/participation').json()['credit_applied'])
    def test_real_video_validation_duration_upload_limit_and_final_invoice_lock(self):
        self.open()
        for seconds,expected in ((2,422),(31,200)):
            p=self.path.parent/('video'+str(seconds)+'.mp4')
            subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=orange:s=160x120:r=2','-t',str(seconds),'-c:v','libx264','-pix_fmt','yuv420p',str(p)],check=True)
            r=self.public.post('/api/participation/video',files={'file':('move.mp4',p.read_bytes(),'video/mp4')},headers={'content-length':'14000000'})
            self.assertEqual(r.status_code,expected,r.text)
        state=self.public.get('/api/participation').json();self.assertGreaterEqual(state['attachment']['duration_seconds'],30)
        self.assertEqual(state['attachment']['scan_status'],'Video checked')
        with transaction(self.path) as db:db.execute("UPDATE job_invoices SET status='Issued' WHERE id='INV'")
        self.assertFalse(self.public.get('/api/participation').json()['video_available'])
        self.assertEqual(self.consent().status_code,409)
        self.assertEqual(self.crew.post('/api/jobs/J1/participation/email',json={'kind':'participation','profile':'main'}).status_code,409)
        self.assertEqual(self.crew.post('/api/jobs/J1/participation/email',json={'kind':'review','profile':'main'}).status_code,200)
    def test_payments_and_withdrawal_block_credit(self):
        self.open();self.attachment();rid=self.consent().json()['id']
        with transaction(self.path) as db:
            db.execute("UPDATE media_releases SET status='Withdrawn' WHERE id=?",(rid,))
        self.assertEqual(self.crew.post('/api/jobs/J1/participation/credit',json={'invoice_id':'INV','version':1}).status_code,409)
        with transaction(self.path) as db:
            db.execute("UPDATE media_releases SET status='Signed' WHERE id=?",(rid,))
            insert(db,'payments',dict(id='FINAL',lead_id='L1',amount_cents=1000,method='Interac e-Transfer',purpose='Balance',reference='TEST',verified=1,recorded_by='owner',created_at=now()))
            insert(db,'invoice_payment_allocations',dict(id='FINALALLOC',invoice_id='INV',payment_id='FINAL',amount_cents=1000,allocated_by='owner',created_at=now()))
        self.assertFalse(self.public.get('/api/participation').json()['video_available'])
        self.assertEqual(self.crew.post('/api/jobs/J1/participation/credit',json={'invoice_id':'INV','version':1}).status_code,409)
    def test_manual_email_preview_recipient_confirmation_and_send_once(self):
        endpoint='/api/jobs/J1/participation/email'
        r=self.crew.post(endpoint,json={'kind':'review','profile':'main'});self.assertEqual(r.status_code,200,r.text);m=r.json()
        self.assertEqual(m['status'],'Draft');self.assertIn('honest review',m['body'])
        self.assertEqual(self.crew.post(endpoint,json={'kind':'review','profile':'main'}).json()['id'],m['id'])
        send=endpoint+'/'+m['id']+'/send'
        self.assertEqual(self.crew.post(send,json={'confirm':True,'recipient':m['recipient']}).status_code,503)
        with patch.dict(os.environ,{'CROWNOPS_ALLOW_EXTERNAL':'1','CROWNOPS_TEST_EMAIL_ALLOWLIST':m['recipient']}),patch('app.providers.send_gmail',return_value='fictional-sent') as mail:
            self.assertEqual(self.crew.post(send,json={'confirm':True,'recipient':'other@example.invalid'}).status_code,409)
            r=self.crew.post(send,json={'confirm':True,'recipient':m['recipient']});self.assertEqual(r.status_code,200,r.text);self.assertTrue(r.json()['sent'])
            self.assertTrue(self.crew.post(send,json={'confirm':True,'recipient':m['recipient']}).json()['already_sent']);self.assertEqual(mail.call_count,1)
        self.assertEqual(self.crew.post(endpoint,json={'kind':'review','profile':'main'}).json()['status'],'Sent')
    def test_draw_is_free_private_unique_owner_controlled_and_one_winner(self):
        did=self.draw();self.assertEqual(self.enter(did).status_code,200);self.assertEqual(self.enter(did).status_code,200)
        entries=self.owner.get('/api/participation/draws/'+did+'/entries').json();self.assertEqual(len(entries),1)
        self.assertNotIn('email',self.public.get('/api/giveaways/'+did).text)
        pick='/api/participation/draws/'+did+'/select-winner'
        self.assertEqual(self.owner.post(pick,json={'confirm':True}).status_code,409)
        future=(date.fromisoformat(business_day())+timedelta(days=191)).isoformat()
        with patch('app.client_participation.business_day',return_value=future):
            self.assertEqual(self.enter(did,'later@example.invalid').status_code,409)
            self.assertEqual(self.owner.post(pick,json={'confirm':True}).status_code,409)
            review='/api/participation/draws/'+did+'/entries/'+entries[0]['id']+'/review'
            self.assertEqual(self.owner.post(review,json={'status':'Eligible','note':'Fictional verified age, residency and single entry.'}).status_code,200)
            r=self.owner.post(pick,json={'confirm':True});self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['paid'])
            self.assertEqual(self.owner.post(pick,json={'confirm':True}).status_code,409)
            self.assertEqual(self.owner.post(review,json={'status':'Ineligible','note':'Cannot alter frozen roster.'}).status_code,409)
        award='/api/participation/draws/'+did+'/award'
        self.assertEqual(self.owner.post(award,json={'payment_reference':'test'}).status_code,422)
        r=self.owner.post(award,json={'qualification_confirmed':True,'payment_reference':'FICTIONAL EXTERNAL PAYMENT'});self.assertEqual(r.status_code,200,r.text);self.assertFalse(r.json()['money_sent'])
        self.assertEqual(self.owner.post(award,json={'qualification_confirmed':True,'payment_reference':'DUPLICATE'}).status_code,409)
        self.assertTrue(any(a['object_type']=='client_draw_entries' for a in self.owner.get('/api/audit').json()))
        self.assertFalse(any(a['object_type'] in ('client_draw_entries','client_draws') for a in self.manager.get('/api/audit').json()))
    def test_review_stats_are_verified_private_all_ratings_and_current_half_year(self):
        for rating in (2,5):
            payload={'job_id':'J1','rating':rating,'review_url':'https://www.google.com/maps/reviews/'+str(rating),'note':'Matched the actual published reviewer and completed move.'}
            self.assertEqual(self.crew.post('/api/participation/reviews',json=payload).status_code,403)
            r=self.manager.post('/api/participation/reviews',json=payload);self.assertEqual(r.status_code,200,r.text)
            self.assertEqual(self.manager.post('/api/participation/reviews',json=payload).status_code,409)
        counts=self.crew.get('/api/my-portal/client-feedback').json();self.assertEqual(counts['total'],2);self.assertEqual(counts['five_star_count'],1)
        self.assertEqual(self.staff('lead').get('/api/my-portal/client-feedback').json()['total'],0)
    def test_no_automatic_review_outreach_and_owner_approval_required(self):
        from app.worker import tick
        with transaction(self.path) as db:
            put_setting(db,'automation_policy',{'enabled':False,'review_request':True})
            insert(db,'outbound_messages',dict(id='OLD',event_key='old-review',lead_id='L1',subject='Old',body='Old',recipient='client@example.invalid',category='Review request',status='Approved',created_by='automation',created_at=now()))
        tick(self.path)
        with closing(connect(self.path)) as db:self.assertEqual(one(db,"SELECT status FROM outbound_messages WHERE id='OLD'")['status'],'Cancelled')
        self.assertEqual(self.manager.post('/api/platform/settings/media_program',json=MEDIA).status_code,403)
        self.assertEqual(self.owner.post('/api/platform/settings/media_program',json={**MEDIA,'release_text_fr':''}).status_code,422)
        self.assertEqual(self.owner.post('/api/platform/settings/media_program',json=MEDIA).status_code,200)
