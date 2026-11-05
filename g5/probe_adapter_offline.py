"""Review draft ONLY: actual job body with path/identity seams, no privilege run.

Not PID1/container/Console proof. No application or accepted controller is edited.
All temporary fictional material is deleted. Never print key/factor/cookie values.
"""
import ast
import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,default=WORK/'pilot-release-rc1')
parser.add_argument('--lifecycle-dir',type=Path,default=WORK/'g5-lifecycle/g5')
parser.add_argument('--launcher-dir',type=Path,default=WORK/'g5-nocli-hardening/g5')
args=parser.parse_args()
SOURCE=args.source.resolve(strict=True)
G5=args.lifecycle_dir.resolve(strict=True)
OLD_G5=args.launcher_dir.resolve(strict=True)
sys.path[:0] = [str(G5), str(OLD_G5), str(SOURCE), str(SOURCE/'tools/pilot/maintenance')]
import lifecycle_entry as entry
import lifecycle_supervisor as lc
import start_fictional_web as launcher

checks=[]
def check(name, value):
    if not value:raise AssertionError(name)
    checks.append(name)

attempts=[]
def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname',
                 'socket.gethostbyaddr','os.system','subprocess.Popen','os.posix_spawn'):
        attempts.append(event);raise RuntimeError('offline probe denied transport')
sys.addaudithook(guard)

os.umask(0o077)
controller_before=hashlib.sha256((G5/'lifecycle_supervisor.py').read_bytes()).hexdigest()
with tempfile.TemporaryDirectory(prefix='g5-actual-adapter-fictional-only-') as tmp:
    root=Path(tmp);private=root/'private';private.mkdir(mode=0o700)
    uploads=root/'uploads';uploads.mkdir(mode=0o700)
    replacements={'/data/private':str(private),'/data/uploads':str(uploads),
                  '/app':str(SOURCE),'/app/tools/pilot/maintenance':str(SOURCE/'tools/pilot/maintenance'),
                  '/opt/g5':str(G5)}
    tree=ast.parse((G5/'lifecycle_supervisor.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='job')
    remapped=[]
    class Paths(ast.NodeTransformer):
        def visit_Constant(self,n):
            if isinstance(n.value,str) and n.value in replacements:
                remapped.append(n.value);return ast.copy_location(ast.Constant(replacements[n.value]),n)
            return n
    node=Paths().visit(node)
    module=ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
    namespace=dict(vars(lc))
    # Only filesystem identity adaptation: all nofollow/mode/link checks execute.
    namespace['PrivateDir']=lambda path:lc.PrivateDir(path,uid=os.getuid())
    exec(compile(module,'<exact-job-body-path-remapped>','exec'),namespace)
    actual_job=namespace['job']
    original_reader=launcher.read_private_key
    def read_fixture_key():return original_reader(str(private/'fixture-app.key'),owner_uid=os.getuid())
    # Privilege proof is intentionally absent. Scope values still pass the real validator.
    with mock.patch.object(entry,'assert_unprivileged',return_value={}), mock.patch.object(launcher,'read_private_key',side_effect=read_fixture_key), mock.patch.dict(os.environ,dict(entry.SCOPE),clear=True):
        import core_maintenance as m
        def run(action,data=None):
            state=private/'lifecycle';state.mkdir(mode=0o700,exist_ok=True)
            if data is not None:
                lc.PrivateDir(state,os.getuid()).create('job-input.json',lc.encode(data))
            actual_job(action,'pilot.sqlite3')
            d=lc.PrivateDir(state,os.getuid())
            result=lc.strict_json(d.read('job-result.json'))
            os.unlink(state/'job-result.json')
            if data is not None:os.unlink(state/'job-input.json')
            return result
        run('provision-fixture')
        db=private/'pilot.sqlite3';key=(private/'fixture-app.key').read_bytes()
        accounts=json.loads((private/'fixture-identities.json').read_text())
        check('exactly two fictional identities',[(a['role'],a['email']) for a in accounts]==[('owner','owner@example.invalid'),('sales','sales@example.invalid')])
        check('separate passwords and MFA factors',accounts[0]['password']!=accounts[1]['password'] and accounts[0]['totp_secret']!=accounts[1]['totp_secret'])
        m.verify_ready(db,key,{'owner':'owner@example.invalid','sales':'sales@example.invalid'})
        check('real MFA decryption readiness',True)
        from app import security
        os.environ['CROWNOPS_SECRET_KEY']=key.decode()
        users=[]
        with contextlib.closing(sqlite3.connect(db)) as c:
            users=c.execute('SELECT id,role FROM users ORDER BY role').fetchall()
        for uid,role in users:
            a=next(a for a in accounts if a['role']==role)
            check(role+' password-only MFA refused',security.verify_user_mfa(db,uid,None) is False)
            # Use a fresh accepted adjacent timestep, without waiting or displaying codes.
            import time
            code=security.totp(a['totp_secret'],int(time.time()//30)+1)
            check(role+' fresh MFA factor accepted',security.verify_user_mfa(db,uid,code) is True)
            check(role+' replay refused',security.verify_user_mfa(db,uid,code) is False)
        os.environ.pop('CROWNOPS_SECRET_KEY')
        from fastapi import FastAPI
        from app.core_v1 import create_app_routes
        app=FastAPI();create_app_routes(app,db,lambda:None,SOURCE,initialize_schema=False)
        create=next(r.endpoint for r in app.routes if getattr(r,'path','')=='/api/core-v1/inquiries' and 'POST' in getattr(r,'methods',set()))
        owner={'id':next(uid for uid,role in users if role=='owner'),'role':'owner'}
        original='FICTIONAL ORIGINAL\r\nDate as submitted: 2027-02-30\r\nKeep this exact text.'
        first=create({'request_key':'G5-ADAPTER-001','client':{'client_name':'Fictional Customer','email':'fictional-customer@example.invalid'},'service':{},'source':{'body':original}},owner)
        create({'request_key':'G5-ADAPTER-002','client_id':first['client_id'],'client':{'client_name':'Fictional Customer','email':'fictional-customer@example.invalid'},'service':{},'source':{'body':original+'\nSecond service.'}},owner)
        # Seed only fictional sessions, preserving the original/key/audit constraints.
        with contextlib.closing(sqlite3.connect(db)) as c:
            columns=[r[1] for r in c.execute('PRAGMA table_info(sessions)')]
        # Session schema is recorded, never guessed for a business database.
        from app.store import connect
        with contextlib.closing(connect(db)) as c:
            for i,(uid,role) in enumerate(users):
                c.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)',
                          ('fictional-token-hash-'+str(i),uid,'fictional-csrf-'+str(i),'2099-01-01T00:00:00Z'))
            c.commit()
        before=m.summary(db)
        with contextlib.closing(sqlite3.connect(db)) as c:
            originals_before=c.execute('SELECT original_body,source_sha256 FROM manual_source_submissions ORDER BY id').fetchall()
            audit_before=c.execute('SELECT * FROM audit ORDER BY id').fetchall()
        check('two nonempty preserved originals seeded',len(originals_before)==2)
        backup=run('backup')
        check('backup returns pinned receipt',len(backup['receipt_sha256'])==64)
        export=private/'export';incoming=private/'import'
        for name in ('archive.enc','receipt.json'):
            lc.PrivateDir(incoming,os.getuid()).create(name,(export/name).read_bytes())
        result=run('verify-retrieved',backup)
        check('actual encrypted readback function passed',result=={})
        recovered=run('restore-copy',backup)
        dest=private/'g5-recovered.sqlite3';after=m.summary(dest)
        check('separate destination created',dest.exists() and db.exists())
        check('source database unchanged',m.summary(db)==before)
        check('restored sessions revoked',before['counts']['sessions']==2 and after['counts']['sessions']==0)
        check('post-revocation fingerprint recorded',recovered['fingerprint']==after['logical_sha256'])
        check('original records preserved',before['originals']==after['originals'])
        with contextlib.closing(sqlite3.connect(dest)) as c:
            check('original bodies byte exact after restore',c.execute('SELECT original_body,source_sha256 FROM manual_source_submissions ORDER BY id').fetchall()==originals_before)
            check('prior audit rows exact after restore',c.execute('SELECT * FROM audit ORDER BY id').fetchall()[:-1]==audit_before)
        check('exactly one recovery audit row',after['counts']['audit']==before['counts']['audit']+1)
        m.integrity(dest);check('integrity and foreign keys passed',True)
        run('select-recovered-copy',recovered)
        check('verified recovered copy selectable',True)
        check('no prohibited business records',all(after['counts'][t]==0 for t in m.EMPTY))
        check('private files retained 0600',all((f.stat().st_mode&0o777)==0o600 for f in private.rglob('*') if f.is_file()))
        baseline=hashlib.sha256(dest.read_bytes()).hexdigest()
        try:run('restore-copy',backup)
        except FileExistsError:check('existing restore destination refused',True)
        else:raise AssertionError('restore overwrite unexpectedly accepted')
        # Test fixture cleanup only: do NOT copy this into controller recovery.
        (private/'lifecycle/job-input.json').unlink()
        check('existing recovered bytes unchanged',hashlib.sha256(dest.read_bytes()).hexdigest()==baseline)
        try:run('verify-retrieved',{'receipt_sha256':'f'*64})
        except ValueError:check('wrong independently pinned receipt refused',True)
        else:raise AssertionError('wrong receipt unexpectedly accepted')
        (private/'lifecycle/job-input.json').unlink()
        raw=(incoming/'archive.enc').read_bytes();tampered=bytearray(raw);tampered[-8]^=1
        (incoming/'archive.enc').write_bytes(tampered)
        try:run('verify-retrieved',backup)
        except ValueError:check('tampered reuploaded ciphertext refused',True)
        else:raise AssertionError('tampered archive unexpectedly accepted')
        (private/'lifecycle/job-input.json').unlink()
        check('negative readback did not alter restored bytes',hashlib.sha256(dest.read_bytes()).hexdigest()==baseline)
        db_evidence={'before':before['counts'],'after':after['counts'],
                     'original_hashes':before['originals'],'post_revocation_fingerprint':recovered['fingerprint']}
check('controller source unchanged',hashlib.sha256((G5/'lifecycle_supervisor.py').read_bytes()).hexdigest()==controller_before)
check('zero observed transport attempts',not attempts)
print(json.dumps({'status':'PASS adapted offline composition only','checks':len(checks),'passed':checks,
 'path_literals_remapped':remapped,'identity_syscalls':'NOT EXECUTED; assertion mocked',
 'controller_sha256':controller_before,'network_attempts':len(attempts),
 'database_readback':db_evidence,
 'real_accounts':0,'runtime_pid1_console_privilege_proof':'NOT TESTED'},indent=2))
