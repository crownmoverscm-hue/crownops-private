"""Recovery tests use disposable databases and fictional private files."""
from contextlib import closing
from pathlib import Path
import json
import os
import tempfile
import unittest
import zipfile
from app.backups import make_backup, restore_archive, verify_backup, inspect_archive
from app.store import initialize, transaction, connect, insert, now


class BackupIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'source'/'crownops.sqlite3'
        initialize(self.db);self.uploads=self.db.parent/'uploads';self.uploads.mkdir()
        self.archive=self.root/'recovery.zip'
        with transaction(self.db) as db:
            insert(db,'users',dict(id='OWNER',email='test@example.invalid',name='Test Owner',role='owner',password_hash='preserved-hash',created_at=now()))
            insert(db,'leads',dict(id='LEAD',client_name='Test Client',created_at=now(),updated_at=now()))
        (self.uploads/'proof.txt').write_text('private-proof')

    def backup(self):
        make_backup(self.db,self.archive,self.uploads)
        return self.archive

    def rewrite(self, changes):
        with zipfile.ZipFile(self.archive) as z:
            contents={n:z.read(n) for n in z.namelist()}
        contents.update(changes)
        with zipfile.ZipFile(self.archive,'w') as z:
            for name,content in contents.items():z.writestr(name,content)

    def test_manifest_and_restore_drill_verify_all_files_and_private_permissions(self):
        result=verify_backup(self.backup())
        self.assertTrue(result['all_files_hashed']);self.assertTrue(result['restore_verified'])
        self.assertEqual(result['upload_files'],1)
        self.assertEqual(self.archive.stat().st_mode & 0o777,0o600)
        restored=self.root/'restored'/'crownops.sqlite3';restore_archive(self.archive,restored)
        self.assertEqual((restored.parent/'uploads'/'proof.txt').read_text(),'private-proof')
        self.assertEqual((restored.parent/'uploads'/'proof.txt').stat().st_mode & 0o777,0o600)
        with closing(connect(restored)) as db:
            self.assertEqual(db.execute("SELECT password_hash FROM users WHERE id='OWNER'").fetchone()[0],'preserved-hash')

    def test_restore_revokes_all_staff_client_and_oauth_links_without_changing_live_data(self):
        with transaction(self.db) as db:
            insert(db,'sessions',dict(token_hash='staff-session',user_id='OWNER',csrf='test',expires_at='2099'))
            insert(db,'invitations',dict(token_hash='staff-invite',email='staff@example.invalid',name='Test Staff',role='sales',expires_at='2099',created_by='OWNER'))
            insert(db,'account_resets',dict(token_hash='reset',user_id='OWNER',expires_at='2099'))
            insert(db,'oauth_states',dict(state_hash='oauth',provider='gmail',user_id='OWNER',expires_at='2099'))
            insert(db,'portal_sessions',dict(token_hash='client-session',lead_id='LEAD',email='client@example.invalid',csrf='test',expires_at='2099',created_at=now()))
            insert(db,'portal_invites',dict(token_hash='client-invite',lead_id='LEAD',email='client@example.invalid',expires_at='2099',created_by='OWNER',created_at=now()))
            insert(db,'auth_factors',dict(user_id='OWNER',secret_ciphertext='preserved-encrypted-factor',enabled=1,created_at=now()))
        self.backup();restored=self.root/'restored'/'crownops.sqlite3';restore_archive(self.archive,restored)
        for path,expected in ((self.db,1),(restored,0)):
            with closing(connect(path)) as db:
                for table in ('sessions','invitations','account_resets','oauth_states','portal_sessions','portal_invites'):
                    self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0],expected,table)
                self.assertEqual(db.execute('SELECT secret_ciphertext FROM auth_factors').fetchone()[0],'preserved-encrypted-factor')

    def test_changed_upload_or_database_is_rejected_before_restore_writes(self):
        for member in ('uploads/proof.txt','database.sqlite3'):
            with self.subTest(member=member):
                self.backup()
                self.rewrite({member:b'changed'})
                target=self.root/'tampered'/'crownops.sqlite3'
                with self.assertRaises(ValueError):restore_archive(self.archive,target)
                self.assertFalse(target.exists())
                self.archive.unlink()

    def test_missing_referenced_upload_fails_without_a_published_archive(self):
        with transaction(self.db) as db:
            insert(db,'attachments',dict(id='FILE',lead_id='LEAD',category='Inventory',original_name='proof.txt',stored_name='missing.txt',mime='text/plain',size=1,sha256='test',author='OWNER',created_at=now()))
        with self.assertRaises(ValueError):self.backup()
        self.assertFalse(self.archive.exists());self.assertEqual(list(self.root.glob('*.partial')),[])

    def test_temporary_uploads_and_symlinks_are_excluded(self):
        (self.uploads/'in-flight.upload').write_text('partial')
        (self.uploads/'link.txt').symlink_to(self.uploads/'proof.txt')
        self.backup()
        with zipfile.ZipFile(self.archive) as z:
            self.assertNotIn('uploads/in-flight.upload',z.namelist());self.assertNotIn('uploads/link.txt',z.namelist())

    def test_missing_database_does_not_create_an_empty_source(self):
        missing=self.root/'missing.sqlite3'
        with self.assertRaises(ValueError):make_backup(missing,self.archive,self.uploads)
        self.assertFalse(missing.exists());self.assertFalse(self.archive.exists())

    def test_existing_backup_and_existing_restore_target_are_preserved(self):
        self.backup();original=self.archive.read_bytes()
        with self.assertRaises(ValueError):self.backup()
        self.assertEqual(self.archive.read_bytes(),original)
        with self.assertRaises(ValueError):restore_archive(self.archive,self.db)
        with self.assertRaises(ValueError):make_backup(self.db,self.uploads/'unsafe.zip',self.uploads)

    def test_legacy_backup_is_accepted_with_weaker_verification_reported(self):
        with zipfile.ZipFile(self.archive,'w') as z:
            z.write(self.db,'database.sqlite3');z.writestr('BACKUP_INFO.json',json.dumps({'created_at':now()}))
        self.assertFalse(verify_backup(self.archive)['all_files_hashed'])

    def test_unsafe_duplicate_and_symlink_members_are_rejected(self):
        for member in ('../escape','uploads/nested/file','uploads/back\\slash'):
            self.backup();self.rewrite({member:b'unsafe'})
            with self.assertRaises(ValueError):inspect_archive(self.archive)
            self.archive.unlink()
        with zipfile.ZipFile(self.archive,'w') as z:
            z.write(self.db,'database.sqlite3');info=zipfile.ZipInfo('uploads/link');info.external_attr=(0o120777 << 16)
            z.writestr(info,'/etc/passwd')
        with self.assertRaises(ValueError):inspect_archive(self.archive)
