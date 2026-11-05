"""Run every tests/test_*.py module in its own fresh Python interpreter.

Why: the Core v1 profile installs process-wide transport guards
(app/core_v1.py install_transport_guards) that intentionally cannot be undone.
Under a single `unittest discover` process those guards leak into later modules
(for example photo/video processing), producing false failures. Each module is
therefore discovered exactly as CI did (`discover -s tests -p <file>`, repository
root as working directory) but in a separate process. The guard is unchanged.

Safety nets so isolation can never hide tests:
  * every module must run at least one test;
  * the aggregate count must meet --min-tests (pinned by CI);
  * any failure, error, unexpected success, crash or timeout fails the run.

Usage: python tests/run_isolated.py [--min-tests N] [--jobs N] [--timeout S] [module ...]
"""
import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / 'tests'
RAN = re.compile(r'^Ran (\d+) tests? in ', re.M)
SUMMARY = re.compile(r'^(OK|FAILED)(?: \((.*)\))?\s*$', re.M)


def modules(selected):
    found = sorted(p.name for p in TESTS.glob('test_*.py'))
    if not selected:
        return found
    wanted = [s if s.endswith('.py') else s + '.py' for s in selected]
    missing = [w for w in wanted if w not in found]
    if missing:
        raise SystemExit('Unknown test module(s): ' + ', '.join(missing))
    return wanted


def run_one(name, timeout):
    started = time.monotonic()
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', name, '-v']
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=os.environ.copy())
        output, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or '') + (exc.stderr or '') if isinstance(exc.stdout, str) else ''
        return {'module': name, 'ran': 0, 'ok': False, 'detail': f'TIMEOUT after {timeout}s', 'seconds': timeout, 'output': output}
    ran = RAN.findall(output)
    summary = SUMMARY.findall(output)
    count = int(ran[-1]) if ran else 0
    detail = summary[-1][1] if summary and summary[-1][1] else ''
    ok = code == 0 and bool(summary) and summary[-1][0] == 'OK' and count > 0
    if code == 0 and count == 0:
        detail = 'no tests ran'
    if 'unexpected success' in detail:
        ok = False
    return {'module': name, 'ran': count, 'ok': ok, 'detail': detail or ('exit ' + str(code) if not ok else ''),
            'seconds': round(time.monotonic() - started, 1), 'output': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('modules', nargs='*')
    parser.add_argument('--min-tests', type=int, default=0, help='fail if fewer tests run in total')
    parser.add_argument('--jobs', type=int, default=1)
    parser.add_argument('--timeout', type=int, default=900, help='per-module timeout in seconds')
    parser.add_argument('--quiet', action='store_true', help='print full output only for failing modules')
    args = parser.parse_args()
    names = modules(args.modules)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda n: run_one(n, args.timeout), names))
    for r in results:
        if not args.quiet or not r['ok']:
            print(f'\n===== {r["module"]} =====\n{r["output"]}', flush=True)
    total = sum(r['ran'] for r in results)
    failed = [r for r in results if not r['ok']]
    print('\n===== Isolated test summary =====')
    for r in results:
        mark = 'ok  ' if r['ok'] else 'FAIL'
        print(f'{mark} {r["module"]:<45} tests={r["ran"]:<4} {r["seconds"]:>6}s {r["detail"]}')
    print(f'modules={len(results)} tests={total} failed_modules={len(failed)} min_tests={args.min_tests}')
    if args.min_tests and total < args.min_tests:
        print(f'FAILED: only {total} tests ran; at least {args.min_tests} are required.')
        return 1
    if failed:
        print('FAILED: ' + ', '.join(r['module'] for r in failed))
        return 1
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
