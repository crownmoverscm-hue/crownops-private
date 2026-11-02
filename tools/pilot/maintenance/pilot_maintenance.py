"""Owner-operated offline CLI. Execution with real accounts/data requires separate approval.
No secret is accepted in argv or printed. Existing private key files only.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
from core_maintenance import key_file,path_check,publish,provision,backup,restore,verify_ready,verify_readback


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='Offline Core v1 maintenance; no server or provider access')
    sub=p.add_subparsers(dest='operation',required=True)
    a=sub.add_parser('provision')
    a.add_argument('--source',required=True); a.add_argument('--database',required=True)
    a.add_argument('--app-key-file',required=True)
    a.add_argument('--owner-email',choices=['jonathanprobonas15@gmail.com'],default='jonathanprobonas15@gmail.com')
    a.add_argument('--sales-email',choices=['crownmovers.cm@gmail.com'],default='crownmovers.cm@gmail.com')
    a=sub.add_parser('backup')
    a.add_argument('--database',required=True); a.add_argument('--archive',required=True)
    a.add_argument('--backup-key-file',required=True); a.add_argument('--app-key-file',required=True); a.add_argument('--uploads-directory',required=True)
    a.add_argument('--receipt',required=True); a.add_argument('--quiescence-record',required=True)
    a=sub.add_parser('restore')
    a.add_argument('--archive',required=True); a.add_argument('--database',required=True)
    a.add_argument('--backup-key-file',required=True); a.add_argument('--app-key-file',required=True)
    a.add_argument('--receipt',required=True); a.add_argument('--recovery-receipt',required=True)
    a=sub.add_parser('verify-ready')
    a.add_argument('--database',required=True); a.add_argument('--app-key-file',required=True)
    a=sub.add_parser('verify-readback')
    a.add_argument('--archive',required=True); a.add_argument('--receipt',required=True)
    a.add_argument('--trusted-receipt',required=True)
    a.add_argument('--backup-key-file',required=True); a.add_argument('--app-key-file',required=True)
    args=p.parse_args()
    if args.operation=='provision':
        if not sys.stdin.isatty() or not sys.stderr.isatty(): raise ValueError('Private interactive terminal required')
        accounts=[]
        for role,email in (('owner',args.owner_email),('sales',args.sales_email)):
            name=input(role+' display name: ')
            password=getpass.getpass(role+' password (local masked entry): ')
            if password!=getpass.getpass('Confirm password: '): raise ValueError('Password confirmation failed')
            # Owner prepares a separate privately enrolled secret in a 0600 file, not a URL/QR service.
            secret_path=input(role+' private TOTP-secret file path: ')
            secret=path_check(secret_path).read_text().strip()
            accounts.append(dict(role=role,email=email,name=name,password=password,totp_secret=secret))
        for account in accounts:
            account['code']=getpass.getpass(account['role']+' current authenticator code: ')
        result=provision(args.database,args.source,accounts,key_file(args.app_key_file))
    elif args.operation=='backup':
        receipt=path_check(args.receipt,False)
        record=json.loads(path_check(args.quiescence_record).read_text())
        if not record.get('operation_id') or record.get('writer_stopped') is not True or record.get('uploads_disabled') is not True:
            raise ValueError('Invalid quiescence evidence record')
        result=backup(args.database,args.archive,key_file(args.backup_key_file),args.uploads_directory,True,operation_id=record['operation_id'],app_key=key_file(args.app_key_file))
        result['operation_id']=record['operation_id']
        # If this publication fails, the archive remains for reconciliation; never blindly replace it.
        publish(receipt,json.dumps(result,sort_keys=True).encode())
    elif args.operation=='verify-ready':
        result=verify_ready(args.database,key_file(args.app_key_file),{'owner':'jonathanprobonas15@gmail.com','sales':'crownmovers.cm@gmail.com'})
    elif args.operation=='verify-readback':
        result=verify_readback(args.archive,key_file(args.backup_key_file),key_file(args.app_key_file),args.receipt,args.trusted_receipt)
    else:
        destreceipt=path_check(args.recovery_receipt,False)
        receipt=json.loads(path_check(args.receipt).read_text())
        result=restore(args.archive,args.database,key_file(args.backup_key_file),key_file(args.app_key_file),receipt)
        publish(destreceipt,json.dumps(result,sort_keys=True).encode())
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try: main()
    except Exception:
        # Never emit exception repr or traceback: secret-bearing input may appear there.
        print('REFUSED: offline maintenance did not complete; reconcile private files and operation receipt before retrying.',file=sys.stderr)
        raise SystemExit(1)
