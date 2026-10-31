"""Export independently inspectable disposable replay results, never a server."""
import hashlib
import json
from pathlib import Path
import sys
from test_original_notification_intake import OriginalNotificationTests, FIXTURES, EMPTY_TABLES
from app.gmail_sync import _quote_fields


def run():
    case=OriginalNotificationTests()
    case.setUp()
    try:
        case.test_four_unchanged_bodies_create_one_client_four_services_and_preserve_every_field()
        tables=('client_accounts','client_account_leads','leads','integration_events','messages','tasks','intake_drafts',*EMPTY_TABLES)
        stored={t:case.records(t) for t in tables}
        retry=case.sync()
        assert retry['duplicates']==4 and retry['new_leads']==retry['new_events']==0
        assert stored=={t:case.records(t) for t in tables}
        rows=[]
        for fixture in FIXTURES:
            key='gmail:OCT24-'+fixture['id']
            lead=next(x for x in stored['leads'] if x['external_id']==key)
            event=next(x for x in stored['integration_events'] if x['event_key']==key)
            parsed=_quote_fields(fixture['body'],fixture['subject'])
            rows.append({'fixture':fixture['id'],'subject':fixture['subject'],
                'source_body':fixture['body'],'body_sha256':hashlib.sha256(fixture['body'].encode()).hexdigest(),
                'parsed':parsed,'stored_lead':lead,'event':event,
                'draft':next(x for x in stored['intake_drafts'] if x['lead_id']==lead['id']),
                'field_comparison':{field:{'parsed':parsed[field],'stored':lead[field],'equal':parsed[field]==lead[field]}
                    for field in ('client_name','email','phone','language','move_date','pickup','dropoff','home_size','boxes','pickup_access','dropoff_access','pickup_walk','dropoff_walk','inventory','notes','heavy_items','photo_link')}})
        return {'scope':'Disposable local rendered-text replay only; no deployment, mailbox or provider qualification',
                'counts':{t:len(stored[t]) for t in tables},'fixtures':rows,'retry':retry,
                'stored_records':stored,'application_external_requests':0,
                'outbound_protection':'requests and socket.connect raise during ingestion; provider API is mocked',
                'limitations':['Original Markdown file byte identity and raw HTML unverified',
                    'FR form addresses intentionally blank pending review',
                    'Calculator language inferred and review-required',
                    'Same Gmail message ID is immutable for idempotency; changed-body retry is ignored, not a 409 conflict',
                    'All four draft preparations blocked; no approved quotes or endpoint acceptance claim']}
    finally:
        case.doCleanups()


if __name__=='__main__':
    output=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/crownops-corrected-intake-results.json')
    result=run();output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print('PASS — one client, four services, source preserved, exact retries unchanged, zero downstream')
    print(json.dumps(result['counts']));print('Results:',output)
