"""Test-only child-process network guard; no credentials are handled by this wrapper."""
import json,runpy,sys
from pathlib import Path
attempts=[]
def guard(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.gethostbyname','socket.gethostbyaddr','subprocess.Popen','os.system','os.posix_spawn'):
        attempts.append(event);raise RuntimeError('Fictional qualification network/process boundary')
sys.addaudithook(guard)
target=sys.argv[1];sys.argv=sys.argv[1:]
sys.path.insert(0,str(Path(target).resolve().parent))
try:
    runpy.run_path(target,run_name='__main__')
finally:
    print('CHILD_NETWORK_COUNTERS '+json.dumps({'attempts':len(attempts),'events':attempts}))
    if attempts:raise SystemExit(99)
