"""Private client files follow current crew assignments, including direct URLs."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import hashlib
import os
import tempfile
import unittest
from fastapi.testclient import TestClient
from app.main import create_app, sha
from app.store import transaction, insert, now


class PrivateFilePermissionTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.path=self.root/'crownops.sqlite3'
        self.uploads=self.root/'uploads'
        env=patch.dict(os.environ,{'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0',
            'CROWNOPS_PUBLIC_URL':'https://crownops.example.invalid','CROWNOPS_SECURE_COOKIES':'1',
            'CROWNOPS_ALLOW_AUTH_EMAIL':'0','CROWNOPS_UPLOADS_ENABLED':'0',
            'CROWNOPS_UPLOADS':str(self.uploads),'CROWNOPS_RUN_WORKER':'0',
            'RAILWAY_PROJECT_ID':'','RAILWAY_PUBLIC_DOMAIN':'','RAILWAY_VOLUME_MOUNT_PATH':''})
        env.start();self.addCleanup(env.stop)
        self.app=create_app(self.path)
        with transaction(self.path) as db:
            for eid in ('E1','E2'):
                insert(db,'employees',dict(id=eid,name=eid,role='Mover'))
            for role in ('owner','sales','dispatch','accounting','crew','unlinked'):
                insert(db,'users',dict(id=role,name=role,email=role+'@example.invalid',
                    role='crew' if role=='unlinked' else role,employee_id='E1' if role=='crew' else None,
                    password_hash='unused-test-password',created_at=now()))
            for lid in ('L1','L2'):
                insert(db,'leads',dict(id=lid,client_name=lid,created_at=now(),updated_at=now()))
            for jid,lid,eid in [('J1','L1','E1'),('J2','L1','E2'),('J3','L2','E2')]:
                insert(db,'jobs',dict(id=jid,lead_id=lid,title=jid,phase='Moving',
                    start_at='2027-01-01T09:00:00+00:00',end_at='2027-01-01T13:00:00+00:00',
                    created_by='owner',created_at=now()))
                insert(db,'assignments',dict(job_id=jid,resource_type='employee',resource_id=eid))
            for aid,lid,jid in [('shared','L1',None),('own','L1','J1'),('other-job','L1','J2'),('other-client','L2','J3')]:
                content=b'private-test-file-'+aid.encode();name=aid+'.jpg'
                (self.uploads/name).write_bytes(content)
                insert(db,'attachments',dict(id=aid,lead_id=lid,job_id=jid,category='Move photos',
                    original_name=name,stored_name=name,mime='image/jpeg',size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),scan_status='Clean',author='owner',created_at=now()))
        self.crew=self.client('crew')

    def client(self,user=None):
        client=TestClient(self.app,base_url='https://crownops.example.invalid');self.addCleanup(client.close)
        if user:
            with transaction(self.path) as db:
                insert(db,'sessions',dict(token_hash=sha('token-'+user),user_id=user,csrf='test-csrf',
                    expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
            client.cookies.set('crownops_session','token-'+user);client.headers['x-csrf-token']='test-csrf'
        return client

    def test_crew_cannot_list_or_download_another_clients_files(self):
        self.assertEqual(self.crew.get('/api/leads/L2/uploads').status_code,403)
        self.assertEqual(self.crew.get('/api/uploads/other-client').status_code,403)

    def test_same_client_files_are_limited_to_shared_and_assigned_job(self):
        response=self.crew.get('/api/leads/L1/uploads')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual({r['id'] for r in response.json()},{'shared','own'})
        self.assertEqual(self.crew.get('/api/uploads/other-job').status_code,403)
        for aid in ('shared','own'):
            r=self.crew.get('/api/uploads/'+aid)
            self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.headers['cache-control'],'no-store')

    def test_current_assignment_removal_revokes_file_access_without_new_login(self):
        self.assertEqual(self.crew.get('/api/uploads/own').status_code,200)
        with transaction(self.path) as db:db.execute("DELETE FROM assignments WHERE job_id='J1'")
        self.assertEqual(self.crew.get('/api/uploads/own').status_code,403)
        self.assertEqual(self.crew.get('/api/leads/L1/uploads').status_code,403)

    def test_unlinked_and_signed_out_staff_cannot_access_private_files(self):
        for client,status in [(self.client('unlinked'),403),(self.client(),401)]:
            self.assertEqual(client.get('/api/uploads/own').status_code,status)
            self.assertEqual(client.get('/api/leads/L1/uploads').status_code,status)

    def test_office_roles_retain_project_file_access(self):
        for role in ('owner','sales','dispatch','accounting'):
            client=self.client(role)
            self.assertEqual(client.get('/api/uploads/other-client').status_code,200,role)
            self.assertEqual(len(client.get('/api/leads/L1/uploads').json()),3,role)

    def test_client_portal_remains_scoped_to_its_own_project(self):
        client=self.client()
        with transaction(self.path) as db:
            insert(db,'portal_sessions',dict(token_hash=sha('portal-token'),lead_id='L1',
                email='client@example.invalid',csrf='test',created_at=now(),
                expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
        client.cookies.set('crownops_client','portal-token')
        self.assertEqual(client.get('/api/portal/uploads/shared').status_code,200)
        self.assertEqual(client.get('/api/portal/uploads/other-client').status_code,404)

    def test_crew_cannot_upload_to_unassigned_client_or_job(self):
        for lid,jid in [('L2','J3'),('L1','J2')]:
            response=self.crew.post('/api/leads/'+lid+'/uploads',data={'job_id':jid},
                files={'file':('test.jpg',b'\xff\xd8\xfftest','image/jpeg')})
            self.assertEqual(response.status_code,403,response.text)
