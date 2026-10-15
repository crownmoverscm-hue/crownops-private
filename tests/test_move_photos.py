"""Real photo conversion and private move/crew access, without external providers."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import os
import struct
import tempfile
import unittest
import zlib

from fastapi.testclient import TestClient
from PIL import Image
from app.main import create_app, sha
from app.security import validate_deployment
from app.store import connect, transaction, insert, one, now


class MovePhotoTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.path=self.root/'crownops.sqlite3';self.uploads=self.root/'uploads'
        env=patch.dict(os.environ,{'CROWNOPS_ENV':'test','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_UPLOADS_ENABLED':'1',
            'CROWNOPS_UPLOAD_MODE':'photos','CROWNOPS_UPLOADS':str(self.uploads),'CROWNOPS_RUN_WORKER':'0',
            'RAILWAY_PROJECT_ID':'','RAILWAY_PUBLIC_DOMAIN':'','RAILWAY_VOLUME_MOUNT_PATH':''})
        env.start();self.addCleanup(env.stop);self.app=create_app(self.path)
        expires=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
        with transaction(self.path) as db:
            for uid,role,eid in [('owner','owner',None),('crew','crew','E1'),('other','crew','E2')]:
                if eid:insert(db,'employees',dict(id=eid,name=uid,role='Mover',active=1,hourly_cents=2500))
                insert(db,'users',dict(id=uid,email=uid+'@example.invalid',name=uid,role=role,employee_id=eid,password_hash='unused',active=1,created_at=now()))
                insert(db,'sessions',dict(token_hash=sha(uid+'-token'),user_id=uid,csrf='test-csrf',expires_at=expires))
            for lid in ('L1','L2'):insert(db,'leads',dict(id=lid,client_name=lid,created_at=now(),updated_at=now()))
            for jid,lid,eid in [('J1','L1','E1'),('J2','L2','E2')]:
                insert(db,'jobs',dict(id=jid,lead_id=lid,title=jid,phase='Moving',start_at='2026-11-15T14:00:00+00:00',end_at='2026-11-15T18:00:00+00:00',created_by='owner',created_at=now()))
                insert(db,'assignments',dict(job_id=jid,resource_type='employee',resource_id=eid))
            insert(db,'portal_sessions',dict(token_hash=sha('client-token'),lead_id='L1',email='client@example.invalid',csrf='client-csrf',expires_at=expires,created_at=now()))
        self.owner=self.staff('owner');self.crew=self.staff('crew');self.other=self.staff('other')
        self.portal=TestClient(self.app);self.addCleanup(self.portal.close)
        self.portal.cookies.set('crownops_client','client-token');self.portal.headers['x-csrf-token']='client-csrf'

    def staff(self,user):
        client=TestClient(self.app);self.addCleanup(client.close)
        client.cookies.set('crownops_session',user+'-token');client.headers['x-csrf-token']='test-csrf'
        return client

    def image(self,fmt='PNG',size=(100,80),exif=False):
        stream=BytesIO();image=Image.new('RGB',size,'#4c7399')
        if exif:
            tags=Image.Exif();tags[270]='private description';tags[274]=6
            image.save(stream,format=fmt,exif=tags)
        else:image.save(stream,format=fmt)
        return stream.getvalue()

    def upload(self,content=None,name='room.png',client=None,jid='J1',lid='L1'):
        return (client or self.owner).post('/api/leads/'+lid+'/uploads',data={'category':'Move photos','job_id':jid},files={'file':(name,content if content is not None else self.image(),'image/png')})

    def count(self):
        with closing(connect(self.path)) as db:return db.execute('SELECT COUNT(*) FROM attachments').fetchone()[0]

    def test_reencodes_all_supported_photo_types_and_serves_inline_without_metadata(self):
        for fmt,ext in [('JPEG','jpg'),('PNG','png'),('WEBP','webp')]:
            result=self.upload(self.image(fmt,(3200,2200),True),name='room.'+ext)
            self.assertEqual(result.status_code,200,result.text);item=result.json()
            self.assertEqual(item['scan_status'],'Photo checked');self.assertEqual(item['mime'],'image/jpeg')
            response=self.crew.get('/api/uploads/'+item['id']);self.assertEqual(response.status_code,200,response.text)
            self.assertIn('inline',response.headers['content-disposition']);self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(response.headers['x-content-type-options'],'nosniff')
            image=Image.open(BytesIO(response.content));self.assertLessEqual(max(image.size),2048);self.assertFalse(image.getexif())
            self.assertNotIn(b'private description',response.content)
            with closing(connect(self.path)) as db:record=one(db,'SELECT * FROM attachments WHERE id=?',(item['id'],))
            self.assertEqual(record['sha256'],hashlib.sha256(response.content).hexdigest())

    def test_project_photo_is_shared_with_assigned_crew_and_revoked_on_unassignment(self):
        response=self.portal.post('/api/portal/uploads',data={'category':'Move photos'},files={'file':('room.png',self.image(),'image/png')})
        self.assertEqual(response.status_code,200,response.text);aid=response.json()['id']
        self.assertEqual(self.crew.get('/api/uploads/'+aid).status_code,200)
        self.assertEqual(self.other.get('/api/uploads/'+aid).status_code,403)
        self.assertEqual(self.portal.get('/api/portal/uploads/'+aid).status_code,200)
        with transaction(self.path) as db:db.execute("DELETE FROM assignments WHERE job_id='J1'")
        self.assertEqual(self.crew.get('/api/uploads/'+aid).status_code,403)

    def test_crew_can_upload_only_to_their_current_job(self):
        own=self.upload(client=self.crew);self.assertEqual(own.status_code,200,own.text)
        self.assertEqual(self.upload(client=self.other).status_code,403)
        self.assertEqual(self.upload(client=self.crew,jid='J2',lid='L2').status_code,403)
        self.assertEqual(self.other.get('/api/uploads/'+own.json()['id']).status_code,403)

    def test_rejects_nonphotos_bad_images_and_empty_files_without_storing_anything(self):
        for content,name in [(b'%PDF-1.7\nnot-a-photo','document.pdf'),(b'\xff\xd8\xffbroken','broken.jpg'),(b'','empty.png'),(self.image(),'script.svg')]:
            result=self.upload(content,name);self.assertEqual(result.status_code,422,result.text)
        self.assertEqual(self.count(),0);self.assertEqual(list(self.uploads.iterdir()),[])

    def test_removes_appended_content_and_deduplicates_repeated_uploads(self):
        original=self.image()+b'<script>untrusted extra bytes</script>'
        first=self.upload(original);self.assertEqual(first.status_code,200,first.text)
        second=self.upload(original);self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(first.json()['id'],second.json()['id']);self.assertEqual(self.count(),1)
        downloaded=self.crew.get('/api/uploads/'+first.json()['id']).content
        self.assertNotIn(b'<script>',downloaded);self.assertEqual(len(list(self.uploads.iterdir())),1)

    def test_storage_headroom_and_quota_fail_without_orphan_files(self):
        with patch('app.photo_safety.shutil.disk_usage',return_value=SimpleNamespace(free=100)):
            self.assertEqual(self.upload().status_code,507)
        with patch.dict(os.environ,{'CROWNOPS_PHOTO_STORAGE_MB':'0'}):
            self.assertEqual(self.upload().status_code,507)
        self.assertEqual(self.count(),0);self.assertEqual(list(self.uploads.iterdir()),[])

    def test_rejects_decompression_bomb_before_decoding(self):
        def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',6000,5000,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'bad'))+chunk(b'IEND',b'')
        self.assertEqual(self.upload(png).status_code,422);self.assertEqual(self.count(),0)

    def test_policy_does_not_enable_general_document_uploads_or_claim_antivirus(self):
        policy=self.owner.get('/api/upload-policy').json();self.assertTrue(policy['enabled']);self.assertEqual(policy['mode'],'photos')
        self.assertNotIn('pdf',policy['accept']);self.assertNotIn('video',policy['accept'])
        self.assertEqual(self.portal.get('/api/portal/project').json()['upload_policy']['mode'],'photos')
        with patch.dict(os.environ,{'CROWNOPS_ENV':'production','CROWNOPS_SECRET_KEY':'MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA='}):
            validate_deployment('https://example.invalid',True)
            with patch.dict(os.environ,{'CROWNOPS_UPLOAD_MODE':'scanned-files','CROWNOPS_SCAN_COMMAND':''}):
                with self.assertRaises(RuntimeError):validate_deployment('https://example.invalid',True)
