"""Standalone CLI qualification: only fictional files/identities, no app import in parent."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,os,subprocess,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
GUARD=ROOT/'tests/guarded_process.py';CLI=ROOT/'maintenance/pilot_maintenance.py'
results=[]
def run(target,*args,ok=True):
    r=subprocess.run([sys.executable,str(GUARD),str(target),*map(str,args)],text=True,capture_output=True)
    lines=[line for line in r.stdout.splitlines() if line.startswith('CHILD_NETWORK_COUNTERS ')]
    assert len(lines)==1 and json.loads(lines[0].split(' ',1)[1])['attempts']==0,(r.returncode,r.stderr)
    assert (r.returncode==0)==ok,(r.returncode,r.stdout,r.stderr)
    results.append({'operation':str(target.name),'exit_code':r.returncode,'network_attempts':0})
    return r
os.umask(0o077)
with tempfile.TemporaryDirectory(prefix='crownops-cli-fictional-') as tmp:
    root=Path(tmp);root.chmod(0o700)
    run(ROOT/'tests/cli_fictional_fixture.py',root)
    archive=root/'offsite/archive.enc';receipt=root/'offsite/receipt.json'
    backup=['backup','--database',root/'source/pilot.sqlite3','--archive',archive,'--backup-key-file',root/'backupkeys/backup.key',
            '--app-key-file',root/'appkeys/app.key','--uploads-directory',root/'uploads','--receipt',receipt,'--quiescence-record',root/'quiescence.json']
    run(CLI,*backup)
    record=json.loads(receipt.read_text())
    assert record['operation_id']=='CLI-FICTIONAL-001'
    assert record['archive_sha256']==hashlib.sha256(archive.read_bytes()).hexdigest()
    assert record['archive_bytes']==archive.stat().st_size
    trusted=root/'offsite/trusted-receipt.json';trusted.write_bytes(receipt.read_bytes());trusted.chmod(0o600)
    run(CLI,'verify-readback','--archive',archive,'--receipt',receipt,'--trusted-receipt',trusted,
        '--backup-key-file',root/'backupkeys/backup.key','--app-key-file',root/'appkeys/app.key')
    restored=root/'restored/pilot.sqlite3';recovery=root/'restored/recovery.json'
    restore=['restore','--archive',archive,'--database',restored,'--backup-key-file',root/'backupkeys/backup.key',
             '--app-key-file',root/'appkeys/app.key','--receipt',receipt,'--recovery-receipt',recovery]
    run(CLI,*restore)
    assert restored.stat().st_mode&0o777==0o600
    assert json.loads(recovery.read_text())['post_revocation']['counts']['sessions']==0
    # Unchanged independently qualified D-01 revoker is executable against this
    # release's recovered fictional schema. Full seeded token matrix is historical.
    run(ROOT/'qualified_d01/d01_revoke_sessions.py','--source-db',restored,
        '--dest-db',root/'restored/standalone-revoked.sqlite3')
    run(CLI,*restore,ok=False) # Existing database never overwritten.
    # Fixed real identities are not created by tests. Readiness gate must reject fictional identities.
    run(CLI,'verify-ready','--database',restored,'--app-key-file',root/'appkeys/app.key',ok=False)
    # Provision refuses piped/non-private input before prompting for secrets or creating records.
    unmade=root/'source/no-terminal.sqlite3'
    run(CLI,'provision','--source',os.environ['CROWNOPS_TEST_SOURCE'],'--database',unmade,'--app-key-file',root/'appkeys/app.key',ok=False)
    assert not unmade.exists()
    # Actual independent-process competing archive publishers, with one archive and one receipt.
    archive2=root/'offsite/concurrent.enc';receipt2=root/'offsite/concurrent.json'
    concurrent=[archive2 if a==archive else receipt2 if a==receipt else a for a in backup]
    def attempt(_):
        r=subprocess.run([sys.executable,str(GUARD),str(CLI),*map(str,concurrent)],text=True,capture_output=True)
        line=[line for line in r.stdout.splitlines() if line.startswith('CHILD_NETWORK_COUNTERS ')]
        assert len(line)==1 and json.loads(line[0].split(' ',1)[1])['attempts']==0
        return r.returncode
    with ThreadPoolExecutor(max_workers=4) as pool:statuses=list(pool.map(attempt,range(4)))
    assert statuses.count(0)==1 and statuses.count(1)==3,statuses
    winning=json.loads(receipt2.read_text())
    assert winning['archive_sha256']==hashlib.sha256(archive2.read_bytes()).hexdigest()
    assert winning['archive_bytes']==archive2.stat().st_size
    print('CLI_PROCESS_RESULTS '+json.dumps({'serial_children':results,'concurrent_children':4,'winner':1,'denied':3,
        'actual_network_attempts':0,'real_accounts_created':False,'receipt_hash_and_size':'matched','real_offsite':'NOT TESTED'}))
