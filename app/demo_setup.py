"""Create an isolated fictional local demo; never import real records here."""
import json,secrets,os
from pathlib import Path
from contextlib import closing
from datetime import date,timedelta
from .store import initialize,connect,transaction,one,rows,now,dumps
from .seed import seed_pricing,seed_fictional
from .main import create_user
from .platform_common import put_setting,settings


def prepare_demo(path):
    path=Path(path);initialize(path);cred=path.parent/'DEMO_LOGINS_PRIVATE.json'
    with closing(connect(path)) as db:
        marker=settings(db,'instance_kind')
        if marker=='isolated-demo' and cred.exists():return json.loads(cred.read_text())
        if one(db,'SELECT id FROM leads LIMIT 1') or one(db,'SELECT id FROM users LIMIT 1'):
            raise ValueError('This is not a fresh demo database. Use its existing accounts or an empty demo folder; do not point the demo at company data.')
    seed_pricing(path);seed_fictional(path)
    people=[]
    for name,email,role,emp in [('Demo Manager','manager@example.invalid','owner',None),('Demo Sales','sales@example.invalid','sales',None),('Demo Mover','mover@example.invalid','crew','DEMO-EMP-1')]:
        password=secrets.token_urlsafe(14);create_user(path,email,name,password,role,emp);people.append({'name':name,'email':email,'password':password,'role':role})
    with transaction(path) as db:
        put_setting(db,'instance_kind','isolated-demo')
        db.execute('UPDATE pricing_versions SET approved=1,approved_by=?,approved_at=?',('fictional-demo-setup',now()))
        # These are examples for the isolated demo, never legal/company approval.
        inv=one(db,"SELECT terms_json FROM job_invoices WHERE id='DEMO-INV-1'")
        if inv:put_setting(db,'billing_policy',{'approved':True,'terms':json.loads(inv['terms_json']),'review_note':'Fictional local demonstration only.'})
        put_setting(db,'client_terms',{'approved':True,'version':'DEMO-ONLY-1','text':'FICTIONAL DEMO. No real move, booking, payment, or agreement is created. These sample terms must be replaced and reviewed before real use.','review_note':'Fictional sample, not legal advice.'})
        put_setting(db,'media_program',{'approved':True,'version':'DEMO-ONLY-1','release_text':'FICTIONAL DEMO ONLY. Example: I voluntarily permit recording and publication of my honest promotional testimonial on my selected channels. I am an adult and can authorize the recording. A $50 promotional participation discount is disclosed. No public review or positive opinion is required. Replace this example with reviewed terms before use.','channels':['Website','Social posts','Advertising'],'credit_mode':'before_tax','review_note':'Fictional example only; not legally reviewed.'})
        today=date.today();anchor=today-timedelta(days=(today.weekday()-3)%7)
        put_setting(db,'payroll_policy',{'approved':True,'period_anchor':anchor.isoformat(),'review_note':'Fictional Thursday grouping; not a payroll compliance configuration.'})
        put_setting(db,'automation_policy',{'enabled':False})
        db.execute("UPDATE employee_directory_profiles SET team_lead=1 WHERE employee_id='DEMO-EMP-1'")
    cred.parent.mkdir(parents=True,exist_ok=True);cred.write_text(json.dumps(people,indent=2));cred.chmod(0o600)
    return people
