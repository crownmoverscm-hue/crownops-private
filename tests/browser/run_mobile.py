"""Run Chromium checks against an isolated, fictional CrownOps installation.

Install Playwright separately; no browser dependency is added to production.
Set NODE_PATH if Playwright is installed outside this repository.
"""
import argparse
import os
from pathlib import Path
import runpy
import socket
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suite', choices=['staff_ai', 'team_chat', 'today_workspace', 'mobile_schedule', 'estimate_delivery', 'browser_recovery', 'mobile_smoke', 'navigation_audit', 'dialog_audit', 'schedule_import', 'app_relaunch', 'connection_continuity', 'document_browser', 'hours_costs', 'wix_sync', 'inbox', 'payroll_periods', 'staff_portal', 'client_participation'],
                        default='mobile_smoke', nargs='?')
    args = parser.parse_args()
    if args.suite=='inbox':os.environ['CROWNOPS_BROWSER_INBOX']='1'
    here = Path(__file__).resolve().parent
    fixture_file=args.suite+'_fixture.py' if args.suite in ('staff_portal','client_participation') else 'fixture.py'
    threading.Thread(target=lambda: runpy.run_path(str(here / fixture_file), run_name='__main__'),
                     daemon=True).start()
    for _ in range(600):
        try:
            with socket.create_connection(('127.0.0.1', 8095), timeout=.1):
                break
        except OSError:
            time.sleep(.1)
    else:
        raise RuntimeError('The fictional browser-test installation did not start')
    return subprocess.run(['node', str(here / (args.suite + '.cjs'))], timeout=300).returncode


if __name__ == '__main__':
    raise SystemExit(main())
