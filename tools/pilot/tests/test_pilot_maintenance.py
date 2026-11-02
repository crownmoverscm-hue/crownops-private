"""Targeted fictional qualification. No customer data; temporary keys and distinct test credentials."""
from pathlib import Path
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import hashlib,io,json,os,sqlite3,sys,tempfile,time,unittest,zipfile
from unittest.mock import patch
TOOL_ROOT=Path(__file__).resolve().parents[1]
ROOT=Path(os.environ.get('CROWNOPS_TEST_SOURCE',str(TOOL_ROOT)))
sys.path.insert(0,str(TOOL_ROOT/'maintenance'))
import core_maintenance as m
from cryptography.fernet import Fernet

ATTEMPTS=[]
def network_guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','subprocess.Popen','os.system','os.posix_spawn'):
        ATTEMPTS.append(event)
        raise RuntimeError('Qualification transport/process denied')
sys.addaudithook(network_guard)

class Qualification(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(prefix='crownops-owner-fictional-'); self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name); os.chmod(self.root,0o700)
        self.db=self.root/'source.sqlite3'; self.appkey=Fernet.generate_key(); self.backkey=Fernet.generate_key()
        self.uploads=self.root/'uploads'; self.uploads.mkdir(mode=0o700)
        from app.security import totp
        self.step=int(time.time()//30)
        import base64,secrets
        self.accounts=[]
        for role in ('owner','sales'):
            secret=base64.b32encode(secrets.token_bytes(20)).decode()
            self.accounts.append(dict(role=role,email=role+'@example.invalid',name='TEST '+role,
                password='FICTIONAL-'+role+'-password-2026!',totp_secret=secret,code=totp(secret,self.step)))
        with patch('time.time',return_value=self.step*30):
            m.provision(self.db,ROOT,self.accounts,self.appkey)
        self.archive=self.root/'archive.enc'

    def bk(self):
        return m.backup(self.db,self.archive,self.backkey,self.uploads,True,app_key=self.appkey)

    def test_01_two_accounts_mfa_no_seeds(self):
        s=m.summary(self.db)
        self.assertEqual(s['counts']['users'],2); self.assertEqual(s['counts']['auth_factors'],2)
        self.assertEqual(s['counts']['audit'],2)
        for table in m.EMPTY: self.assertEqual(s['counts'][table],0,table)
        m.scope(self.db,self.appkey)
        identities={a['role']:a['email'] for a in self.accounts}
        self.assertEqual(m.verify_ready(self.db,self.appkey,identities)['enabled_mfa_factors'],2)
        self.assertEqual(set(m.verify_ready(self.db,self.appkey,identities)['staff_ids']),{'owner','sales'})
        with self.assertRaises(ValueError): m.verify_ready(self.db,self.appkey,{'owner':'wrong@example.invalid','sales':'sales@example.invalid'})
        with self.assertRaises(Exception): m.verify_ready(self.db,Fernet.generate_key(),identities)
        self.assertEqual(self.db.stat().st_mode&0o777,0o600)

    def test_02_invalid_enrollment_or_duplicate_identity_no_database(self):
        for kind in ('code','email','password','totp_secret','role'):
            a=[dict(x) for x in self.accounts]
            if kind=='code': a[1]['code']='invalid'
            elif kind=='role': a[1]['role']='crew'
            else: a[1][kind]=a[0][kind]
            dest=self.root/('invalid-'+kind+'.db')
            with self.assertRaises(ValueError): m.provision(dest,ROOT,a,self.appkey)
            self.assertFalse(dest.exists())

    def test_03_key_and_path_privacy(self):
        k=self.root/'key'; k.write_bytes(self.backkey); k.chmod(0o600)
        self.assertEqual(m.key_file(k),self.backkey)
        k.chmod(0o644)
        with self.assertRaises(ValueError): m.key_file(k)
        link=self.root/'link'; link.symlink_to(self.db)
        with self.assertRaises(ValueError): m.path_check(link)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError): m.path_check(self.db)
        self.root.chmod(0o700)

    def test_04_foreign_key_integrity_failure_refused(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO sessions VALUES('fictional','missing-user','csrf','2099')"); db.commit()
        with self.assertRaises(ValueError): self.bk()
        self.assertFalse(self.archive.exists())

    def test_05_scope_and_upload_failure_refused(self):
        (self.uploads/'fictional-file').write_text('TEST')
        with self.assertRaises(ValueError): self.bk()
        self.assertFalse(self.archive.exists())
        (self.uploads/'fictional-file').unlink()
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO oauth_credentials(provider,ciphertext,updated_at) VALUES('TEST','TEST','TEST')"); db.commit()
        with self.assertRaises(ValueError): self.bk()
        self.assertFalse(self.archive.exists())

    def test_06_no_overwrite_parallel_and_sequential(self):
        dest=self.root/'publication'
        def attempt(n):
            try: m.publish(dest,('TEST '+str(n)).encode()); return 'published'
            except FileExistsError: return 'denied'
        with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(attempt,range(8)))
        self.assertEqual(results.count('published'),1); self.assertEqual(results.count('denied'),7)
        before=dest.read_bytes()
        self.assertEqual(attempt(9),'denied'); self.assertEqual(dest.read_bytes(),before)
        print('PUBLICATION parallel=8 winner=1 denied=7 sequential_retry=denied bytes_unchanged=true')

    def test_07_roundtrip_and_revocation(self):
        from app.core_pilot import create_pilot_app
        from app.security import totp
        from app.main import COOKIE
        from fastapi.testclient import TestClient
        env={'CROWNOPS_PROFILE':'core-v1','CROWNOPS_ENV':'pilot','CROWNOPS_ALLOW_EXTERNAL':'0',
             'CROWNOPS_RUN_WORKER':'0','CROWNOPS_REQUIRE_MFA':'1','CROWNOPS_PILOT_ACK':'manual-inquiry-only','CROWNOPS_SECURE_COOKIES':'1',
             'CROWNOPS_PUBLIC_URL':'https://pilot.example.invalid','CROWNOPS_DB':str(self.db),
             'CROWNOPS_SECRET_KEY':self.appkey.decode(),'CROWNOPS_UPLOADS_ENABLED':'0'}
        with patch.dict(os.environ,env),patch('time.time',return_value=(self.step+2)*30):
            with TestClient(create_pilot_app(),base_url='https://pilot.example.invalid') as client:
                for a in self.accounts:
                    base={'email':a['email'],'password':a['password']}
                    self.assertEqual(client.post('/api/login',json=base).status_code,401)
                    self.assertEqual(client.post('/api/login',json={**base,'mfa_code':'invalid'}).status_code,401)
                    good={**base,'mfa_code':totp(a['totp_secret'],self.step+2)}
                    r=client.post('/api/login',json=good); self.assertEqual(r.status_code,200,r.text)
                    self.assertEqual(r.json()['user']['role'],a['role'])
                    self.assertEqual(client.post('/api/login',json=good).status_code,401)
                csrf=r.json()['csrf']; headers={'X-CSRF-Token':csrf}
                cid=''; first=''
                for n in (1,2):
                    payload={'request_key':f'CORE-MAIL-00{n}','client_id':cid,
                        'client':{'client_name':'TEST Client','email':'client@example.invalid','phone':'514-555-0101'},
                        'service':{'service_type':'Moving','move_date':'2027-06-10','pickup':'TEST A','dropoff':'TEST B'},
                        'source':{'kind':'Email','subject':'TEST original','body':f'  TEST original {n}\r\nÉvidence.'}}
                    r=client.post('/api/core-v1/inquiries',json=payload,headers=headers)
                    self.assertEqual(r.status_code,200,r.text); cid=r.json()['client_id']
                    if n==1: first=r.json()['inquiry_id']
                self.assertEqual(client.post(f'/api/core-v1/inquiries/{first}/notes',headers=headers,
                    json={'request_key':'NOTE-001','body':'TEST internal note'}).status_code,200)
                self.assertEqual(client.post(f'/api/core-v1/inquiries/{first}/tasks',headers=headers,
                    json={'request_key':'TASK-001','title':'TEST follow-up','due_date':'2027-06-11'}).status_code,200)
                old_cookie=client.cookies.get(COOKIE)
                self.assertEqual(client.post('/api/quotes/TEST/accept',json={},headers=headers).status_code,403)
                self.assertEqual(client.post('/api/security/mfa-setup',json={},headers=headers).status_code,403)
                self.assertTrue(client.app.state.core_v1_workers_disabled)
        counts=m.summary(self.db)['counts']
        for name,n in {'client_accounts':1,'leads':2,'manual_source_submissions':2,'messages':3,'tasks':3,'users':2,'auth_factors':2}.items():
            self.assertEqual(counts[name],n,name)
        receipt=self.bk(); dest=self.root/'restored.sqlite3'
        with patch('time.time',return_value=(self.step+4)*30):
            recovered=m.restore(self.archive,dest,self.backkey,self.appkey,receipt)
        self.assertEqual(recovered['pre_revocation'],receipt['summary'])
        self.assertEqual(recovered['sessions_revoked'],2)
        self.assertEqual(recovered['post_revocation']['counts']['sessions'],0)
        self.assertEqual(recovered['pre_revocation']['originals'],recovered['post_revocation']['originals'])
        with closing(m.ro(self.db)) as src,closing(m.ro(dest)) as dst:
            for table in ('leads','messages','tasks','manual_source_submissions','users','client_accounts','client_account_leads'):
                self.assertEqual(src.execute('SELECT * FROM '+table).fetchall(),dst.execute('SELECT * FROM '+table).fetchall(),table)
            source_audit=src.execute('SELECT * FROM audit ORDER BY id').fetchall()
            self.assertEqual(dst.execute('SELECT * FROM audit ORDER BY id').fetchall()[:-1],source_audit)
        env['CROWNOPS_DB']=str(dest)
        with patch.dict(os.environ,env),patch('time.time',return_value=(self.step+40)*30):
            with TestClient(create_pilot_app(),base_url='https://pilot.example.invalid',client=('fictional-restored-client',50001)) as client:
                client.cookies.set(COOKIE,old_cookie)
                self.assertEqual(client.get('/api/me').status_code,401)
                for a in self.accounts:
                    base={'email':a['email'],'password':a['password']}
                    self.assertEqual(client.post('/api/login',json=base).status_code,401)
                    self.assertEqual(client.post('/api/login',json={**base,'mfa_code':totp(a['totp_secret'],self.step+2)}).status_code,401)
                    r=client.post('/api/login',json={**base,'mfa_code':totp(a['totp_secret'],self.step+40)})
                    self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(client.post('/api/forgot-password',json={}).status_code,403)
                self.assertTrue(client.app.state.core_v1_workers_disabled)
            from app.store import connect
            with closing(connect(dest)) as db:
                for sql in ("UPDATE audit SET action='TEST'",'DELETE FROM audit','UPDATE manual_source_submissions SET original_body=\'TEST\''):
                    with self.assertRaises(sqlite3.DatabaseError): db.execute(sql)
        print('TWO_ACCOUNT_MFA roles=owner,sales missing_invalid_replay=denied fresh=accepted')
        print('CRM_READBACK clients=1 inquiries=2 originals=2 messages=3 tasks=3 prohibited=0')
        print('RESTORE old_sessions_revoked=2 old_cookie=401 fresh_mfa_both=200 original_and_business_rows=unchanged audit_prefix=unchanged')
        # A second backup/restore starts from a deliberately changed post-revocation database.
        archive2=self.root/'second.enc'
        rec2=m.backup(dest,archive2,self.backkey,self.uploads,True,app_key=self.appkey)
        out2=self.root/'second.sqlite3'
        result2=m.restore(archive2,out2,self.backkey,self.appkey,rec2)
        self.assertEqual(result2['pre_revocation'],rec2['summary'])
        self.assertEqual(result2['pre_revocation']['originals'],receipt['summary']['originals'])

    def test_08_corruption_wrong_keys_and_receipts_no_destination(self):
        receipt=self.bk()
        for label,key,appkey,rec in (
            ('wrong-backup',Fernet.generate_key(),self.appkey,receipt),
            ('wrong-app',self.backkey,Fernet.generate_key(),receipt),
            ('same-key',self.backkey,self.backkey,receipt),
            ('bad-receipt',self.backkey,self.appkey,{**receipt,'archive_sha256':'TEST'}),
            ('bad-size',self.backkey,self.appkey,{**receipt,'archive_bytes':receipt['archive_bytes']+1})):
            dest=self.root/(label+'.db')
            with self.assertRaises(Exception): m.restore(self.archive,dest,key,appkey,rec)
            self.assertFalse(dest.exists())
        damaged=bytearray(self.archive.read_bytes()); damaged[len(damaged)//2]^=1
        tampered=self.root/'tampered.enc'; tampered.write_bytes(damaged); tampered.chmod(0o600)
        for rec in (receipt,{**receipt,'archive_sha256':m.sha(bytes(damaged))}):
            dest=self.root/'cipher-failed.db'
            with self.assertRaises(Exception): m.restore(tampered,dest,self.backkey,self.appkey,rec)
            self.assertFalse(dest.exists())
        data,manifest=m.unpack(self.archive.read_bytes(),self.backkey)
        data=data[:-1]+bytes([data[-1]^1]); buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('manifest.json',json.dumps(manifest)); z.writestr('snapshot.db',data)
        token=Fernet(self.backkey).encrypt(buf.getvalue()); fake=self.root/'stale-manifest.enc';fake.write_bytes(token);fake.chmod(0o600)
        dest=self.root/'manifest-failed.db'
        with self.assertRaises(ValueError): m.restore(fake,dest,self.backkey,self.appkey,{**receipt,'archive_sha256':m.sha(token),'archive_bytes':len(token)})
        self.assertFalse(dest.exists())
        with self.assertRaises(FileExistsError): m.restore(self.archive,self.db,self.backkey,self.appkey,receipt)
        with self.assertRaises(FileExistsError): self.bk()
        with self.assertRaises(FileNotFoundError): m.restore(self.root/'absent',dest,self.backkey,self.appkey,receipt)

    def test_09_wal_snapshot_and_readback_simulation(self):
        with closing(sqlite3.connect(self.db)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO audit(actor,action,object_type,object_id,details_json,created_at) VALUES('TEST','WAL committed','TEST','TEST','{}','TEST')")
            writer.commit()
            for suffix in ('-wal','-shm'):
                p=Path(str(self.db)+suffix)
                if p.exists(): p.chmod(0o600)
            # No concurrent writer during capture: connection remains idle to retain sidecars.
            receipt=self.bk()
        remote=self.root/'simulated-offsite'; remote.mkdir(mode=0o700)
        copied=remote/'archive.enc'; m.publish(copied,self.archive.read_bytes())
        restored=self.root/'offsite-restored.db'
        result=m.restore(copied,restored,self.backkey,self.appkey,receipt)
        self.assertEqual(result['pre_revocation'],receipt['summary'])
        print('OFFSITE_READBACK local_copy_simulation=true ciphertext_sha256=matched real_offsite=NOT_TESTED')

    def test_10_interrupted_publication_reconciles_without_overwrite(self):
        # Inject failure before atomic link: no final file; private staging cleaned.
        with patch.object(m.os,'link',side_effect=OSError('TEST interrupted before publication')):
            with self.assertRaises(OSError): self.bk()
        self.assertFalse(self.archive.exists())
        self.assertFalse(list(self.root.glob('.publish-*')))
        # Directory fsync failure happens after the link: outcome is uncertain but file must be preserved.
        real_sync=m.os.fsync
        calls=[]
        def interrupted(fd):
            calls.append(fd)
            if len(calls)==2: raise OSError('TEST interrupted after publication')
            return real_sync(fd)
        with patch.object(m.os,'fsync',side_effect=interrupted):
            with self.assertRaises(OSError): self.bk()
        self.assertTrue(self.archive.exists())
        previous=self.archive.read_bytes()
        data,manifest=m.unpack(previous,self.backkey)
        self.assertEqual(m.sha(data),manifest['snapshot_sha256'])
        with self.assertRaises(FileExistsError): self.bk()
        self.assertEqual(self.archive.read_bytes(),previous)
        self.assertFalse(list(self.root.glob('.publish-*')))
        print('INTERRUPTION before_link=no_archive after_link=preserved_authenticated_archive retry=denied reconciliation=required')

    def test_11_concurrent_complete_backups_no_receipt_clobber(self):
        def attempt(n):
            try: return ('published',self.bk())
            except FileExistsError: return ('denied',None)
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(attempt,range(4)))
        winners=[r for state,r in results if state=='published']
        self.assertEqual(len(winners),1)
        self.assertEqual(sum(state=='denied' for state,r in results),3)
        receipt=winners[0]
        self.assertEqual(m.sha(self.archive.read_bytes()),receipt['archive_sha256'])
        # An existing receipt is never overwritten, even if it belongs to another operation.
        dest=self.root/'receipt.json';m.publish(dest,b'TEST existing receipt')
        with self.assertRaises(FileExistsError): m.publish(dest,json.dumps(receipt).encode())
        self.assertEqual(dest.read_bytes(),b'TEST existing receipt')
        print('BACKUP_CONCURRENCY parallel=4 winner=1 denied=3 receipt_overwrite=denied')

    def test_12_reusable_tokens_and_mfa_loss_refuse_before_publication(self):
        with self.assertRaises(ValueError): m.backup(self.db,self.archive,self.backkey,self.uploads,True,app_key=self.backkey)
        with self.assertRaises(Exception): m.backup(self.db,self.archive,self.backkey,self.uploads,True,app_key=Fernet.generate_key())
        self.assertFalse(self.archive.exists())
        from app.security import totp
        # Additional reusable bearer tokens are outside this fresh pilot scope: refuse, never partially revoke.
        with closing(sqlite3.connect(self.db)) as db:
            uid=db.execute("SELECT id FROM users WHERE role='owner'").fetchone()[0]
            db.execute("INSERT INTO account_resets VALUES('TEST-only-token',?,'2099',NULL)",(uid,));db.commit()
        with self.assertRaises(ValueError): self.bk()
        self.assertFalse(self.archive.exists())
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('DELETE FROM account_resets');db.execute('DELETE FROM auth_factors WHERE user_id=?',(uid,));db.commit()
        with self.assertRaises(ValueError): self.bk()
        self.assertFalse(self.archive.exists())
        print('EXTRA_REUSABLE_TOKENS refused=true missing_MFA_factor refused=true published_archive=false')

    def test_14_readback_trusted_receipt_and_no_publication(self):
        receipt=self.bk()
        trusted=self.root/'trusted.json'; downloaded=self.root/'downloaded.json'
        data=json.dumps(receipt).encode();trusted.write_bytes(data); downloaded.write_bytes(data)
        trusted.chmod(0o600); downloaded.chmod(0o600)
        before=set(self.root.iterdir())
        result=m.verify_readback(self.archive,self.backkey,self.appkey,downloaded,trusted)
        self.assertTrue(result['trusted_receipt_matched']);self.assertFalse(result['published_database'])
        self.assertEqual(result['archive_bytes'],self.archive.stat().st_size)
        self.assertEqual(set(self.root.iterdir()),before)
        with self.assertRaises(ValueError):m.verify_readback(self.archive,self.backkey,self.appkey,trusted,trusted)
        downloaded.write_bytes(data+b' ')
        with self.assertRaises(ValueError):m.verify_readback(self.archive,self.backkey,self.appkey,downloaded,trusted)
        downloaded.write_bytes(data)
        damaged={**receipt,'archive_bytes':receipt['archive_bytes']+1}
        trusted.write_text(json.dumps(damaged));downloaded.write_text(json.dumps(damaged))
        with self.assertRaises(ValueError):m.verify_readback(self.archive,self.backkey,self.appkey,downloaded,trusted)
        print('READBACK trusted_receipt=matched ciphertext_sha_size=matched integrity=ok FK=0 publication=none real_offsite=NOT_TESTED')

    def test_15_source_pins_and_archive_binding_fail_closed(self):
        for name,expected in m.SOURCE_HASHES.items():
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),expected)
        dest=self.root/'wrong-source.db'
        with patch.dict(m.SOURCE_HASHES,{'app/security.py':'TEST-wrong-hash'}):
            with self.assertRaisesRegex(ValueError,'fingerprint'):m.provision(dest,ROOT,self.accounts,self.appkey)
        self.assertFalse(dest.exists())
        receipt=self.bk()
        self.assertEqual(receipt['source_hashes'],m.SOURCE_HASHES)
        target=self.root/'wrong-pin-restore.db'
        bad={**receipt,'source_hashes':{}}
        with self.assertRaises(ValueError):m.restore(self.archive,target,self.backkey,self.appkey,bad)
        self.assertFalse(target.exists())
        print('RELEASE_PINS require2=matched wrong_source=refused receipt_source_mismatch=refused')

    def test_13_foreign_key_invalid_snapshot_rejected_on_restore(self):
        receipt=self.bk();data,manifest=m.unpack(self.archive.read_bytes(),self.backkey)
        invalid=self.root/'invalid-snapshot.db';invalid.write_bytes(data);invalid.chmod(0o600)
        with closing(sqlite3.connect(invalid)) as db:
            db.execute("INSERT INTO sessions VALUES('TEST-invalid-fk','TEST-missing-user','TEST','2099')");db.commit()
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertTrue(db.execute('PRAGMA foreign_key_check').fetchall())
        altered=invalid.read_bytes();manifest.update(snapshot_sha256=m.sha(altered),snapshot_size=len(altered),summary=m.summary(invalid))
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            z.writestr('manifest.json',json.dumps(manifest));z.writestr('snapshot.db',altered)
        token=Fernet(self.backkey).encrypt(buf.getvalue());archive=self.root/'foreign-key-invalid.enc';archive.write_bytes(token);archive.chmod(0o600)
        updated={**receipt,'archive_sha256':m.sha(token),'archive_bytes':len(token),'snapshot_sha256':manifest['snapshot_sha256'],'summary':manifest['summary']}
        dest=self.root/'refused-restore.db'
        with self.assertRaisesRegex(ValueError,'foreign keys'):m.restore(archive,dest,self.backkey,self.appkey,updated)
        self.assertFalse(dest.exists())
        print('RESTORE_FOREIGN_KEY invalid_snapshot=refused authenticated_manifest=valid destination=absent')

if __name__=='__main__':
    os.umask(0o077)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Qualification)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('NETWORK_COUNTERS '+json.dumps({'actual_attempts':len(ATTEMPTS),'events':ATTEMPTS}))
    raise SystemExit(0 if result.wasSuccessful() and not ATTEMPTS else 1)
