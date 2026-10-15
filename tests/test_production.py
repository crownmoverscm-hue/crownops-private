"""Production launch checks never use real hosting credentials or company data."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import fcntl
import os
import tempfile
import unittest
from unittest.mock import patch
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from app.main import create_app, sha
from app.production import storage_paths, validate_runtime_storage
from app.store import connect, transaction, insert, now
from app.platform_common import settings


class ProductionLaunchTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name);self.path=self.root/'data'/'crownops.sqlite3'
        env={'CROWNOPS_ENV':'test','CROWNOPS_PUBLIC_URL':'https://crownops.example.invalid',
             'CROWNOPS_SECURE_COOKIES':'1','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_ALLOW_AUTH_EMAIL':'0',
             'CROWNOPS_UPLOADS_ENABLED':'0','CROWNOPS_UPLOADS':str(self.path.parent/'uploads'),
             'CROWNOPS_BACKUPS':str(self.path.parent/'backups'),'CROWNOPS_RUN_WORKER':'0',
             'CROWNOPS_SECRET_KEY':Fernet.generate_key().decode(),'GOOGLE_CLIENT_ID':'','GOOGLE_CLIENT_SECRET':'',
             'RAILWAY_PROJECT_ID':'','RAILWAY_PUBLIC_DOMAIN':'','RAILWAY_VOLUME_MOUNT_PATH':''}
        p=patch.dict(os.environ,env);p.start();self.addCleanup(p.stop)
        self.app=create_app(self.path)
        with transaction(self.path) as db:
            for role in ('owner','sales','dispatch','crew','accounting'):
                insert(db,'users',dict(id=role,name='Test '+role,email=role+'@example.invalid',role=role,password_hash='test-hash',created_at=now()))
        self.owner=self.client('owner');self.public=TestClient(self.app,base_url='https://crownops.example.invalid');self.addCleanup(self.public.close)

    def client(self,role):
        with transaction(self.path) as db:
            insert(db,'sessions',dict(token_hash=sha('token-'+role),user_id=role,csrf='csrf-'+role,
                                     expires_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()))
        client=TestClient(self.app,base_url='https://crownops.example.invalid');self.addCleanup(client.close)
        client.cookies.set('crownops_session','token-'+role);client.headers['x-csrf-token']='csrf-'+role
        return client

    def test_hosting_checks_show_unverified_storage_and_separate_hosting_requirements(self):
        response=self.owner.get('/api/hosting-status');self.assertEqual(response.status_code,200,response.text)
        data=response.json();checks={c['key']:c for c in data['checks']}
        self.assertFalse(checks['storage']['ready']);self.assertFalse(data['automated_checks_passed'])
        self.assertFalse(checks['backup']['ready']);self.assertTrue(checks['https']['ready'])
        self.assertTrue(data['hosting_verification_required']);self.assertNotIn(os.environ['CROWNOPS_SECRET_KEY'],response.text)

    def test_hosting_and_backup_creation_and_download_are_owner_only_and_csrf_protected(self):
        for role in ('sales','dispatch','crew','accounting'):
            client=self.client(role)
            self.assertEqual(client.get('/api/hosting-status').status_code,403)
            self.assertEqual(client.post('/api/owner-backups',json={}).status_code,403)
            self.assertEqual(client.get('/api/owner-backups/test.zip').status_code,403)
        self.assertEqual(self.public.get('/api/hosting-status').status_code,401)
        self.assertEqual(self.public.post('/api/owner-backups',json={}).status_code,401)
        self.owner.headers.pop('x-csrf-token')
        self.assertEqual(self.owner.post('/api/owner-backups',json={}).status_code,403)

    def test_owner_backup_is_restore_verified_downloadable_and_reused_for_one_minute(self):
        response=self.owner.post('/api/owner-backups',json={});self.assertEqual(response.status_code,200,response.text)
        record=response.json();self.assertTrue(record['restore_verified']);self.assertTrue(record['available'])
        download=self.owner.get('/api/owner-backups/'+record['filename'])
        self.assertEqual(download.status_code,200);self.assertEqual(download.headers['content-type'],'application/zip')
        self.assertEqual(download.headers['cache-control'],'no-store');self.assertTrue(download.content.startswith(b'PK'))
        self.assertEqual(self.owner.post('/api/owner-backups',json={}).json()['filename'],record['filename'])
        check=next(c for c in self.owner.get('/api/hosting-status').json()['checks'] if c['key']=='backup')
        self.assertTrue(check['ready'])
        with closing(connect(self.path)) as db:
            self.assertEqual(settings(db,'verified_backup')['filename'],record['filename'])
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='Recovery backup created and restore verified'").fetchone()[0],1)

    def test_changed_or_missing_backup_cannot_be_downloaded(self):
        record=self.owner.post('/api/owner-backups',json={}).json();file=storage_paths(self.path)[2]/record['filename']
        content=bytearray(file.read_bytes());content[-1]^=1;file.write_bytes(content)
        self.assertEqual(self.owner.get('/api/owner-backups/'+record['filename']).status_code,409)
        replacement=self.owner.post('/api/owner-backups',json={}).json()
        self.assertNotEqual(replacement['filename'],record['filename'])
        self.assertEqual(self.owner.get('/api/owner-backups/'+replacement['filename']).status_code,200)
        file.unlink();self.assertEqual(self.owner.get('/api/owner-backups/'+record['filename']).status_code,404)

    def test_backup_failure_does_not_record_success_or_publish_an_archive(self):
        with patch('app.production.verify_backup',side_effect=ValueError('test validation failure')):
            response=self.owner.post('/api/owner-backups',json={})
        self.assertEqual(response.status_code,503);self.assertNotIn('test validation failure',response.text)
        self.assertEqual(list(storage_paths(self.path)[2].glob('*.zip')),[])
        with closing(connect(self.path)) as db:self.assertEqual(settings(db,'verified_backup',{}),{})

    def test_concurrent_backup_is_rejected_without_a_second_copy(self):
        directory=storage_paths(self.path)[2];directory.mkdir()
        with (directory/'.backup.lock').open('w') as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.owner.post('/api/owner-backups',json={}).status_code,409)
        self.assertEqual(list(directory.glob('*.zip')),[])

    def test_low_disk_backup_is_refused_before_snapshot_or_archive_creation(self):
        from types import SimpleNamespace
        with patch('app.production.shutil.disk_usage',return_value=SimpleNamespace(free=128*1024*1024)),patch('app.production.make_backup') as make:
            response=self.owner.post('/api/owner-backups',json={})
        self.assertEqual(response.status_code,507,response.text);make.assert_not_called()
        self.assertEqual(list(storage_paths(self.path)[2].glob('*.zip')),[])
        with closing(connect(self.path)) as db:self.assertEqual(settings(db,'verified_backup',{}),{})

    def test_low_disk_does_not_remove_an_existing_verified_recovery_copy(self):
        from types import SimpleNamespace
        from app.platform_common import put_setting
        record=self.owner.post('/api/owner-backups',json={}).json()
        with transaction(self.path) as db:
            old=settings(db,'verified_backup');old['verified_at']='2000-01-01T00:00:00+00:00';put_setting(db,'verified_backup',old)
        with patch('app.production.shutil.disk_usage',return_value=SimpleNamespace(free=128*1024*1024)):
            response=self.owner.post('/api/owner-backups',json={})
        self.assertEqual(response.status_code,507,response.text)
        self.assertEqual(self.owner.get('/api/owner-backups/'+record['filename']).status_code,200)

    def test_production_railway_guard_requires_existing_database_and_all_paths_on_volume(self):
        os.environ.update(CROWNOPS_ENV='production',RAILWAY_PROJECT_ID='test-project',RAILWAY_VOLUME_MOUNT_PATH=str(self.path.parent))
        self.assertTrue(storage_paths(self.path)[3]);validate_runtime_storage(self.path)
        with self.assertRaises(RuntimeError):validate_runtime_storage(self.path.parent/'missing.sqlite3')
        os.environ['CROWNOPS_UPLOADS']=str(self.root/'ephemeral-uploads')
        with self.assertRaises(RuntimeError):validate_runtime_storage(self.path)
        os.environ['RAILWAY_VOLUME_MOUNT_PATH']=''
        with self.assertRaises(RuntimeError):validate_runtime_storage(self.path)
        self.assertFalse((self.path.parent/'missing.sqlite3').exists())

    def test_production_guard_resolves_symlinks_outside_the_volume(self):
        other=self.root/'outside.sqlite3';other.write_bytes(b'outside')
        link=self.path.parent/'linked.sqlite3';link.symlink_to(other)
        os.environ.update(CROWNOPS_ENV='production',RAILWAY_PROJECT_ID='test-project',RAILWAY_VOLUME_MOUNT_PATH=str(self.path.parent))
        with self.assertRaises(RuntimeError):validate_runtime_storage(link)

    def test_all_active_owners_need_mfa_and_stale_backup_is_not_current(self):
        with transaction(self.path) as db:
            insert(db,'auth_factors',dict(user_id='owner',secret_ciphertext='test-encrypted',enabled=1,created_at=now()))
        self.assertTrue(next(c for c in self.owner.get('/api/hosting-status').json()['checks'] if c['key']=='mfa')['ready'])
        self.owner.post('/api/owner-backups',json={})
        from app.platform_common import put_setting
        with transaction(self.path) as db:
            record=settings(db,'verified_backup');record['verified_at']='2000-01-01T00:00:00+00:00';put_setting(db,'verified_backup',record)
            insert(db,'users',dict(id='owner2',name='Test Owner 2',email='owner2@example.invalid',role='owner',password_hash='test',created_at=now()))
        checks={c['key']:c for c in self.owner.get('/api/hosting-status').json()['checks']}
        self.assertFalse(checks['mfa']['ready']);self.assertFalse(checks['backup']['ready'])

    def test_railway_healthcheck_hostname_is_allowed_without_disclosing_backup_or_secrets(self):
        client=TestClient(self.app,base_url='http://healthcheck.railway.app');self.addCleanup(client.close)
        response=client.get('/api/health');self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn('backup',response.text);self.assertNotIn(os.environ['CROWNOPS_SECRET_KEY'],response.text)
