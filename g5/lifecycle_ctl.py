"""Local Console control-file publisher. No new token, socket or public endpoint.

Requires actual UID/GID10001, no retained capabilities, and strict private files.
Private filesystem access authorizes a TRUSTED operator, not hostile same-UID code.
Never prints identities, keys, passwords, MFA factors, database rows or cookies.
"""
import argparse
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lifecycle_entry import assert_unprivileged, validate_scope
from lifecycle_supervisor import (APPROVAL, PrivateDir, encode, latest_status,
                                  validate_request)


def publish_request(directory, action, approval, digest=None, operation=None):
    current = latest_status(directory)
    data = {}
    if action in ('verify-retrieved','restore-copy'):
        data['receipt_sha256'] = digest
    elif action == 'select-recovered-copy':
        data['fingerprint'] = digest
    elif digest is not None:
        raise ValueError('unexpected digest')
    r = {'format':1,'operation':operation or uuid.uuid4().hex,
         'boot':current['boot'],'version':current['version'],'action':action,
         'approval':approval,'scope':'fictional-g5-only','data':data}
    validate_request(r)
    directory.create('request-'+r['operation']+'.json', encode(r))
    return {'operation':r['operation'],'boot':r['boot'],'version':r['version'],
            'submitted':True, 'completed':False}


def main():
    try:
        assert_unprivileged(); validate_scope(os.environ)
        os.umask(0o077)
        p = argparse.ArgumentParser(description='Fictional G5 private lifecycle control')
        p.add_argument('action', choices=('status','provision-fixture','verify-fixture',
            'start-web','stop-web','backup','verify-retrieved','restore-copy',
            'select-recovered-copy','shutdown','receipt'))
        p.add_argument('--approve', choices=(APPROVAL,))
        p.add_argument('--digest')
        p.add_argument('--operation')
        a = p.parse_args()
        d = PrivateDir('/data/private/lifecycle')
        if a.action == 'status':
            result = latest_status(d)
        elif a.action == 'receipt':
            if not a.operation or len(a.operation) != 32:
                raise ValueError('operation')
            from lifecycle_supervisor import ID, strict_json
            if not ID.fullmatch(a.operation):
                raise ValueError('operation')
            result = strict_json(d.read('receipt-'+a.operation+'.json'))
        else:
            if a.approve != APPROVAL:
                raise ValueError('explicit transition approval required')
            result = publish_request(d, a.action, a.approve, a.digest, a.operation)
        print(encode(result).decode('ascii'))
        return 0
    except Exception:
        print('REFUSED: G5 control request not accepted.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
