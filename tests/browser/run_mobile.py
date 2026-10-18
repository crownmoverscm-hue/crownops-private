"""Run Chromium checks against an isolated, fictional CrownOps installation.

Install Playwright separately; no browser dependency is added to production.
Set NODE_PATH if Playwright is installed outside this repository.
"""
import argparse
from pathlib import Path
import runpy
import socket
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suite', choices=['mobile_smoke', 'navigation_audit', 'dialog_audit', 'schedule_import', 'app_relaunch', 'connection_continuity', 'document_browser', 'hours_costs', 'wix_sync'],
                        default='mobile_smoke', nargs='?')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    threading.Thread(target=lambda: runpy.run_path(str(here / 'fixture.py'), run_name='__main__'),
                     daemon=True).start()
    for _ in range(100):
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
