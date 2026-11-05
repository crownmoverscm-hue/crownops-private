"""Fictional development records only. No accounts or passwords are seeded."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json
from .store import transaction, insert, uid, now, dumps
from .business_zone import BUSINESS_ZONE, zone_name

ROOT=Path(__file__).resolve().parent.parent

def seed_pricing(path):
    src=json.loads((ROOT/'config/workbook_pricing_snapshot.json').read_text())
    with transaction(path) as db:
        if db.execute('SELECT count(*) FROM pricing_versions').fetchone()[0]:return
        from .company_profile import company_profile
        # Crown's workbook rates belong only to the original installation (COM-4).
        if company_profile(db)['mode']!='legacy-crown':return
        insert(db,'pricing_versions',dict(id='PRICE-WORKBOOK-SNAPSHOT',label='Workbook seasonal rates - owner review required',
            rates_json=dumps(src['rates']),settings_json=dumps(src['settings']),created_at=now()))

def seed_fictional(path):
    seed_pricing(path)
    with transaction(path) as db:
        if db.execute('SELECT count(*) FROM leads').fetchone()[0] or db.execute('SELECT count(*) FROM imports').fetchone()[0]:
            raise ValueError('Fictional data may only be seeded into an empty client database. Use a separate CROWNOPS_DB.')
        t=now();base=datetime.now(BUSINESS_ZONE).date();sunday=base-timedelta(days=(base.weekday()+1)%7)
        names=['Alex Laurent','Sophie Martin','Jordan Ellis','Camille Roy','Morgan Lee','Taylor Reed','Noah Bennett','Emilie Carter']
        stages=['New Lead','Details Requested','Estimate Ready','Quote Sent','Accepted','Booked','Follow-Up','New Lead']
        for i,name in enumerate(names):
            date=(sunday+timedelta(days=i%7)).isoformat()
            l=dict(id='DEMO-LEAD-'+str(i+1),external_id='FICTIONAL-'+str(i+1),client_name=name,
                   email=name.lower().replace(' ','.')+'@example.invalid',phone='+1 514 555 '+str(100+i).zfill(4),
                   move_date=date,pickup='Example pickup '+str(i+1)+', Montreal (fictional)',
                   dropoff='Example destination '+str(i+1)+', Montreal (fictional)',home_size='2 bedroom apartment',
                   boxes='25 to 35 boxes',inventory='Queen bed and mattress, sofa, two desks, two dressers, 30 packed boxes.',
                   pickup_access='Elevator; booking to confirm',dropoff_access='One flight of stairs',
                   pickup_walk='10 to 20 feet',dropoff_walk='Truck parks close to entrance',
                   language='French' if i in (1,3,7) else 'English',source='Fictional preview',lead_type='Form',
                   stage=stages[i],priority='High' if i==0 else 'Normal',assigned_to='Jonathan' if i<4 else '',
                   created_at=t,updated_at=t,notes='FICTIONAL DEVELOPMENT RECORD - do not contact.')
            insert(db,'leads',l)
            insert(db,'tasks',dict(id='DEMO-TASK-'+str(i+1),lead_id=l['id'],title=['Confirm elevator and access','Review inventory','Follow up on estimate'][i%3],due_date=base.isoformat(),owner='Jonathan' if i<4 else '',created_at=t))
        for i,n in enumerate(['Driver One','Driver Two','Mover Three','Mover Four','Mover Five','Mover Six']):
            insert(db,'employees',dict(id='DEMO-EMP-'+str(i+1),name=n,role='Driver / mover' if i<2 else 'Mover',driver_verified=int(i<2),hourly_cents=2500 if i<2 else 2250))
        truck_colors=['#3B82F6','#22C55E','#F97316','#8B5CF6','#14B8A6','#EF4444','#EAB308','#94A3B8']
        for i,color in enumerate(truck_colors):
            insert(db,'vehicles',dict(id='DEMO-TRUCK-'+str(i+1),name='Demo Truck '+str(i+1),active=1,notes='Fictional vehicle; capacity not a fleet record.',color_hex=color,display_order=i+1))
        phases=['Moving','Packing','Delivery','Moving','Moving','Junk removal','Delivery','Delivery']
        day_offsets=[0,1,2,3,4,5,0,1]
        start_hours=[8,8,8,8,8,8,14,14]
        service_blocks=[7,5,4,6,5,3,1,1]
        truck_numbers=[1,2,3,4,5,6,1,2]
        for i in range(8):
            start=datetime.combine(sunday+timedelta(days=day_offsets[i]),datetime.min.time()).replace(hour=start_hours[i],tzinfo=BUSINESS_ZONE)
            jid='DEMO-JOB-'+str(i+1)
            insert(db,'jobs',dict(id=jid,lead_id='DEMO-LEAD-'+str(i+1),title=names[i],phase=phases[i],
                start_at=start.astimezone(timezone.utc).isoformat(),end_at=(start+timedelta(hours=service_blocks[i])).astimezone(timezone.utc).isoformat(),status='Tentative',
                notes='Fictional job for interface testing. No booking exists.',exception_reason='Fictional demo schedule only.',created_by='demo-seed',created_at=t))
            crew=['DEMO-EMP-1','DEMO-EMP-3','DEMO-EMP-4'] if truck_numbers[i]==1 else ['DEMO-EMP-2','DEMO-EMP-5','DEMO-EMP-6']
            for rid in crew:
                insert(db,'assignments',dict(job_id=jid,resource_type='employee',resource_id=rid))
            insert(db,'assignments',dict(job_id=jid,resource_type='vehicle',resource_id='DEMO-TRUCK-'+str(truck_numbers[i])))

        # Representative accepted estimates make the private demo useful for the real Crown workflow.
        # These values are fictional and never leave the isolated demo database.
        for lead_no,job_no,low,high in [(1,1,5,7),(2,2,4,4),(4,4,4,6),(7,7,1,1),(8,8,1,1)]:
            lid=f'DEMO-LEAD-{lead_no}'; qid=f'DEMO-QUOTE-{lead_no}'
            leadrow=db.execute('SELECT * FROM leads WHERE id=?',(lid,)).fetchone()
            qinputs={'lead_id':lid,'move_date':leadrow['move_date'],'movers':3,'trucks':1,'hours_low':low,'hours_high':high,'transport_hours':1,'deposit_required':300}
            qresult={'movers':3,'trucks':1,'service_low':low,'service_high':high,'hourly_rate_cents':17500,'minimum_hours':3,'transport_hours':1,'transport_cents':17500,'fuel_cents':3500,'discount_cents':0,'other_cents':0,'low_cents':int((low*175+210)*100),'high_cents':int((high*175+210)*100)}
            insert(db,'quotes',dict(id=qid,lead_id=lid,revision=1,status='Accepted',inputs_json=dumps(qinputs),result_json=dumps(qresult),pricing_id='PRICE-WORKBOOK-SNAPSHOT',lead_snapshot_json=dumps(dict(leadrow)),lead_version=leadrow['version'],approved_by='demo-seed',approved_at=t,acceptance_ref='Fictional demo acceptance',accepted_at=t,created_by='demo-seed',created_at=t))
            db.execute('UPDATE jobs SET quote_id=? WHERE id=?',(qid,f'DEMO-JOB-{job_no}'))
        insert(db,'payments',dict(id='DEMO-DEPOSIT-1',lead_id='DEMO-LEAD-1',amount_cents=30000,method='Interac',purpose='Deposit',reference='FICTIONAL-DEPOSIT-001',verified=1,recorded_by='demo-seed',created_at=t))

        for i,(n,q,m,c,sell) in enumerate([('Medium boxes',80,30,145,350),('Wardrobe boxes',20,10,700,1500),('Moving blankets',120,50,800,0),('Mattress bags',8,15,400,1000),('Bubble wrap',15,8,1800,3500)]):
            insert(db,'inventory',dict(id='DEMO-STOCK-'+str(i+1),name=n,category='Demo supply',quantity=q,minimum=m,cost_cents=c,sell_cents=sell,raw_json='{}'))
        insert(db,'messages',dict(id=uid('MSG'),lead_id='DEMO-LEAD-2',kind='Incoming pasted email',subject='Access details',body='Bonjour! Tout sera emballe. Il reste a confirmer la reservation de l\'ascenseur.',author='demo-seed',language='French',created_at=t))

        # End-of-job invoicing sample. Never run this seed on real client data.
        from .invoices import validate_terms
        terms=validate_terms({'issuer':{'name':'Crown Movers - FICTIONAL PILOT','address':'Example office, Montreal','email':'office@example.invalid','phone':'5145550100'},
             'customer':{'name':'Camille Roy (sample)','address':'Example destination 4, Montreal','email':'camille.roy@example.invalid'},
             'hourly_rate':175,'minimum_hours':3,'transport_amount':175,'fuel_amount':35,'discount_amount':0,
             'taxes':[{'name':'Sample tax A','rate_percent':5,'registration':'TEST-A'},{'name':'Sample tax B','rate_percent':9.975,'registration':'TEST-B'}],
             'catalogue':[{'code':'mattress_bag','name':'Mattress protector (sample)','unit_price':12.99},{'code':'box','name':'Medium box (sample)','unit_price':3.5}],
             'approval_reference':'Fictional demonstration terms only. Not approved for real clients.'})
        db.execute("UPDATE jobs SET status='Completed' WHERE id='DEMO-JOB-4'")
        insert(db,'job_invoices',dict(id='DEMO-INV-1',job_id='DEMO-JOB-4',number='TEST-INV-001',status='Draft',terms_json=dumps(terms),
            actual_json=dumps({'service_hours':4,'quantities':{'mattress_bag':1,'box':0},'notes':'Fictional invoice test. No client will receive this.','reason':'Sample actual hours'}),
            terms_approved_by='demo-seed',terms_approved_at=t,created_by='demo-seed',created_at=t,updated_at=t))
