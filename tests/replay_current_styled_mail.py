"""Export only the new sanitized current-layout results; no listener/provider."""
import json
from pathlib import Path
import sys
from app.gmail_sync import _quote_fields,_clean_html
from test_current_styled_mail import CurrentStyledMailTests,FIXTURE


def run(output):
    case=CurrentStyledMailTests();case.setUp()
    try:
        case.test_synthetic_current_layout_recovers_fields_and_records_review_without_downstream()
        records=case.records();retry=case.sync();assert retry['duplicates']==1 and records==case.records()
        parsed=_quote_fields(FIXTURE['html'],FIXTURE['subject'])
        result={'scope':'NEW synthetic current-layout fixture only; lower Move Summary labels are modelled, not screenshot-verified',
            'provenance':FIXTURE['provenance'],'parsed':parsed,'counts':{k:len(v) for k,v in records.items()},
            'records':records,'retry':retry,'external_requests':0,
            'stored_body_preservation':'Normalized rendered text equals _clean_html(synthetic HTML). Raw HTML/MIME retention is not claimed.',
            'comparison':{k:{'parsed':parsed[k],'stored':records['leads'][0][k],'equal':parsed[k]==records['leads'][0][k]}
                for k in ('client_name','email','phone','move_date','pickup','dropoff','notes')}}
        Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print('PASS — new synthetic current layout; exact retry unchanged; zero downstream')
        print(result['counts'])
    finally:case.doCleanups()


if __name__=='__main__':run(sys.argv[1])
