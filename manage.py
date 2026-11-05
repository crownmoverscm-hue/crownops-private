#!/usr/bin/env python3
"""Owner-run administration. No credentials, invites, or production defaults are shipped."""
import argparse,getpass,json,os,sqlite3,sys,time,zipfile
from pathlib import Path
from contextlib import closing
ROOT=Path(__file__).resolve().parent
if (ROOT/'.env').exists():
    for line in (ROOT/'.env').read_text().splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line:continue
        k,v=line.split('=',1)
        if k.strip().isidentifier():os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
from app.store import initialize,connect,transaction,audit,one
from app.seed import seed_pricing
p=argparse.ArgumentParser(description='CrownOps unified application administration')
p.add_argument('--db',default=os.getenv('CROWNOPS_DB',str(ROOT/'instance/crownops.sqlite3')))
sub=p.add_subparsers(dest='command',required=True)
s=sub.add_parser('init',help='Create an owner account with an interactive password; source rates remain unapproved')
s.add_argument('--email',required=True);s.add_argument('--name',required=True)
s=sub.add_parser('serve',help='Run a single application process behind an HTTPS proxy for shared use')
s.add_argument('--host',default='127.0.0.1');s.add_argument('--port',type=int,default=int(os.getenv('PORT','8080')))
s=sub.add_parser('import',help='Import workbook inquiries into historical review without messages');s.add_argument('workbook')
s=sub.add_parser('import-schedule',help='Import schedule source rows into read-only review');s.add_argument('workbook');s.add_argument('--start',default='2026-09-27');s.add_argument('--end',default='2027-12-31')
s=sub.add_parser('backup',help='Back up database and immutable uploads to a NEW private ZIP');s.add_argument('destination')
s=sub.add_parser('restore',help='Restore to an EMPTY database/instance path only');s.add_argument('archive')
s=sub.add_parser('verify-backup',help='Verify checksums and restore privately to a disposable directory');s.add_argument('archive')
s=sub.add_parser('reset-mfa',help='Emergency local account recovery after verifying identity');s.add_argument('--email',required=True);s.add_argument('--reason',required=True)
s=sub.add_parser('worker',help='Generate approved-rule tasks and unsent drafts; no automatic external writes');s.add_argument('--once',action='store_true');s.add_argument('--interval',type=int,default=60)
s=sub.add_parser('doctor',help='Show configuration switches without exposing secrets')
s=sub.add_parser('company-profile',help='Set the company identity used in client emails and documents (new installations must run this)')
s.add_argument('--name',required=True);s.add_argument('--phone',required=True);s.add_argument('--email',required=True);s.add_argument('--timezone',required=True)
s.add_argument('--short-name',default='');s.add_argument('--time-label',default='');s.add_argument('--time-label-fr',default='')
a=p.parse_args();path=Path(a.db).expanduser().resolve()
def _company_configured(db_path):
    if not db_path.exists():return False
    from app.company_profile import company_profile
    with closing(connect(db_path)) as db:return company_profile(db)['configured']
try:
    if a.command=='verify-backup':
        from app.backups import verify_backup
        print(json.dumps(verify_backup(Path(a.archive)),indent=2));sys.exit(0)
    if a.command=='restore':
        if path.exists():raise ValueError('Restoration never overwrites a database. Stop the server and choose a NEW empty instance path.')
        from app.backups import restore_archive
        print(restore_archive(Path(a.archive),path));sys.exit(0)
    if a.command not in ('serve','backup','doctor'):
        initialize(path)
    if a.command=='init':
        from app.main import create_user
        password=getpass.getpass('New owner password (12+ characters): ')
        if password!=getpass.getpass('Confirm password: '):raise ValueError('Passwords do not match.')
        create_user(path,a.email,a.name,password);seed_pricing(path)
        print('Owner created. Sign in and review pricing and operating rules. No messages or bookings were sent.')
        with closing(connect(path)) as db:
            from app.company_profile import company_profile
            if not company_profile(db)['configured']:print('Next: set your company identity with `manage.py company-profile` before sending any client email.')
    elif a.command=='company-profile':
        from fastapi import HTTPException
        from app.company_profile import company_profile,store_profile
        data={'name':a.name,'short_name':a.short_name,'phone':a.phone,'email':a.email,'timezone':a.timezone,'local_time_label':a.time_label,'local_time_label_fr':a.time_label_fr}
        try:
            with transaction(path) as db:
                data['version']=company_profile(db)['version'];saved=store_profile(db,data,'owner-cli')
        except HTTPException as ex:raise ValueError(ex.detail)
        print(json.dumps({k:saved[k] for k in ('name','short_name','phone','email','timezone','version','configured')},indent=2))
    elif a.command=='serve':
        import uvicorn
        from app.main import create_app
        trusted=os.getenv('CROWNOPS_TRUSTED_PROXY_IPS','').strip()
        uvicorn.run(create_app(path),host=a.host,port=a.port,access_log=False,proxy_headers=bool(trusted),forwarded_allow_ips=trusted or '127.0.0.1')
    elif a.command=='import':
        from app.importer import import_workbook
        print(json.dumps(import_workbook(path,Path(a.workbook).read_bytes(),Path(a.workbook).name,'owner-cli'),indent=2))
    elif a.command=='import-schedule':
        from app.schedule_importer import import_schedule
        print(json.dumps(import_schedule(path,Path(a.workbook).read_bytes(),Path(a.workbook).name,'owner-cli',a.start,a.end),indent=2))
    elif a.command=='backup':
        from app.backups import make_backup
        print(make_backup(path,Path(a.destination),Path(os.getenv('CROWNOPS_UPLOADS',str(path.parent/'uploads')))))
    elif a.command=='reset-mfa':
        if len(a.reason)<12:raise ValueError('Provide a documented identity-verification reason.')
        if input('Type RESET after verifying the account owner: ')!='RESET':raise ValueError('Cancelled.')
        with transaction(path) as db:
            u=one(db,'SELECT id FROM users WHERE lower(email)=?',(a.email.lower(),))
            if not u:raise ValueError('Account not found.')
            db.execute('DELETE FROM auth_factors WHERE user_id=?',(u['id'],));db.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],));audit(db,'local-server-owner','Emergency MFA reset','users',u['id'],{'reason':a.reason})
        print('MFA reset; sessions revoked. Require a fresh MFA enrollment.')
    elif a.command=='worker':
        from app.worker import tick
        if a.interval<30:raise ValueError('Use an interval of at least 30 seconds.')
        while True:
            print(json.dumps(tick(path)),flush=True)
            if a.once:break
            time.sleep(a.interval)
    elif a.command=='doctor':
        print(json.dumps({'environment':os.getenv('CROWNOPS_ENV','test'),'public_url':os.getenv('CROWNOPS_PUBLIC_URL','http://127.0.0.1:8080'),'database':str(path),'external_actions_enabled':os.getenv('CROWNOPS_ALLOW_EXTERNAL')=='1','quickbooks_environment':os.getenv('QBO_ENV','sandbox'),'quickbooks_sandbox_writes':os.getenv('CROWNOPS_ALLOW_QBO_SANDBOX_WRITES')=='1','quickbooks_production_writes':os.getenv('CROWNOPS_ALLOW_QBO_PRODUCTION_WRITES')=='1','server_secret_present':bool(os.getenv('CROWNOPS_SECRET_KEY')),'scanner_configured':bool(os.getenv('CROWNOPS_SCAN_COMMAND')),'secure_cookies':os.getenv('CROWNOPS_SECURE_COOKIES')=='1','company_profile_configured':_company_configured(path),'notice':'Configuration flags are not proof of tested/live integrations.'},indent=2))
except (ValueError,sqlite3.Error,OSError,zipfile.BadZipFile) as ex:
    print('Error: '+str(ex),file=sys.stderr);sys.exit(1)
except KeyboardInterrupt:print('\nStopped.')
