"""Host-side trusted fictional Docker orchestration, no host/business bind mounts."""
import fcntl,json,os,subprocess,tempfile,time,uuid
from pathlib import Path
E=Path('evidence/g5');E.mkdir(parents=True,exist_ok=True)
image='crownops-g5-qualified:test';suffix=uuid.uuid4().hex[:10];vol='crownops-g5-'+suffix;web='crownops-g5-web-'+suffix
containers=[];volumes=[];events=[]
def docker(*args,ok=True,capture=True):
    r=subprocess.run(['docker',*args],text=True,capture_output=True,timeout=120)
    if ok and r.returncode:raise RuntimeError('Docker operation failed: '+args[0]+' '+r.stderr[-500:])
    return r
def common(volume,user='10001:10001'):
    a=['run','--rm','--network=none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,mode=1777','--security-opt','no-new-privileges','--cap-drop=ALL','--user',user,'--mount','type=volume,src='+volume+',dst=/data,volume-nocopy']
    if user=='0:0':a+=['--cap-add=CHOWN','--cap-add=FOWNER','--cap-add=DAC_OVERRIDE']
    return a
context={'CROWNOPS_BOOTSTRAP_ACK':'fresh-empty-core-v1-volume-only','CROWNOPS_PROFILE':'core-v1','CROWNOPS_ENV':'pilot','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_RUN_WORKER':'0','CROWNOPS_REQUIRE_MFA':'1','RAILWAY_VOLUME_MOUNT_PATH':'/data'}
for i,kind in enumerate(('PROJECT','ENVIRONMENT','SERVICE'),1):
    value='00000000-0000-4000-8000-'+str(i).zfill(12);context['CROWNOPS_BOOTSTRAP_'+kind+'_ID']=value;context['RAILWAY_'+kind+'_ID']=value
def bootstrap(volume,changes=None,ok=True):
    a=common(volume,'0:0')
    for k,v in {**context,**(changes or {})}.items():a+=['-e',k+'='+v]
    return docker(*a,'--entrypoint','python',image,'/opt/g5/prepare_empty_volume.py',ok=ok)
def active(volume):return docker('ps','-q','--filter','volume='+volume).stdout.strip()
def start(envfile,name=web):
    assert not active(vol)
    a=common(vol);a.remove('--rm');a[1:1]=['-d','--name',name];containers.append(name)
    docker(*a,'--env-file',str(envfile),image)
    for _ in range(60):
        if docker('exec',name,'python','/opt/g5/http_probe.py','health',ok=False).returncode==0:return
        if docker('inspect','--format','{{.State.Running}}',name).stdout.strip()!='true':break
        time.sleep(0.5)
    raise RuntimeError('Locked Uvicorn failed startup')
def stop(name):
    docker('stop','--time','10',name)
    assert docker('inspect','--format','{{.State.Running}}',name).stdout.strip()=='false'
    assert not active(vol)
    events.append({'stage':'writer_stop','running_volume_users':0})
def main():
    docker('volume','create',vol);volumes.append(vol)
    b=bootstrap(vol);events.append({'stage':'bootstrap','report':json.loads(b.stdout)})
    docker(*common(vol),'--entrypoint','python',image,'/opt/g5/container_fixture.py','provision')
    # Fictional app key only, never printed in evidence, command args or logs.
    key=docker(*common(vol),'--entrypoint','python',image,'-c',"from pathlib import Path;print(Path('/data/private/fixture-app.key').read_text())").stdout.strip()
    env={'CROWNOPS_PROFILE':'core-v1','CROWNOPS_ENV':'pilot','CROWNOPS_ALLOW_EXTERNAL':'0','CROWNOPS_RUN_WORKER':'0','CROWNOPS_PILOT_ACK':'manual-inquiry-only','CROWNOPS_SECURE_COOKIES':'1','CROWNOPS_REQUIRE_MFA':'1','CROWNOPS_PUBLIC_URL':'https://g5.example.invalid','CROWNOPS_UPLOADS_ENABLED':'0','CROWNOPS_DB':'/data/private/pilot.sqlite3','CROWNOPS_SECRET_KEY':key}
    with tempfile.TemporaryDirectory(prefix='g5-private-host-') as td:
        envfile=Path(td)/'fictional.env';envfile.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n');envfile.chmod(0o600)
        lockfile=Path(td)/'volume-operation.lock'
        with lockfile.open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);start(envfile)
            events.append(json.loads(docker('exec',web,'python','/opt/g5/http_probe.py','initial').stdout))
            # Both the cooperative operation lock and actual running mount users
            # deny maintenance. An unrestricted Docker admin is outside scope.
            assert active(vol)
            with lockfile.open('w') as competing:
                try:fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:events.append({'stage':'maintenance_while_web','denied_before_container_launch':True})
                else:raise AssertionError('Exclusive operation lock failed')
            stop(web);fcntl.flock(lock,fcntl.LOCK_UN)
        with lockfile.open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);assert not active(vol)
            result=docker(*common(vol),'--entrypoint','python',image,'/opt/g5/container_fixture.py','maintain')
            events.append(json.loads(result.stdout));assert not active(vol)
        # Populated volume is never re-chowned/repaired by the bootstrap.
        refusal=bootstrap(vol,ok=False);assert refusal.returncode!=0
        events.append({'stage':'populated_bootstrap','refused':True})
        for label,value in [('missing',None),('invalid','0')]:
            bad=dict(env)
            if value is None:bad.pop('CROWNOPS_REQUIRE_MFA')
            else:bad['CROWNOPS_REQUIRE_MFA']=value
            f=Path(td)/(label+'.env');f.write_text('\n'.join(k+'='+v for k,v in bad.items())+'\n');f.chmod(0o600)
            refused=docker(*common(vol),'--env-file',str(f),image,ok=False);assert refused.returncode!=0
            events.append({'stage':label+'_MFA_startup','refused':True,'fallback':False})
        docker(*common(vol),'--entrypoint','python',image,'-c',"import os;os.chmod('/data/private/pilot.sqlite3',0o644)")
        assert docker(*common(vol),'--env-file',str(envfile),image,ok=False).returncode!=0
        docker(*common(vol),'--entrypoint','python',image,'-c',"import os;os.chmod('/data/private/pilot.sqlite3',0o600);os.symlink('/data/private/pilot.sqlite3','/data/private/link.sqlite3')")
        linked=dict(env);linked['CROWNOPS_DB']='/data/private/link.sqlite3';lf=Path(td)/'symlink.env';lf.write_text('\n'.join(k+'='+v for k,v in linked.items())+'\n');lf.chmod(0o600)
        assert docker(*common(vol),'--env-file',str(lf),image,ok=False).returncode!=0
        docker(*common(vol),'--entrypoint','python',image,'-c',"import os;os.unlink('/data/private/link.sqlite3')")
        events.append({'stage':'unsafe_DB_storage','mode_0644':'refused','symlink':'refused','repaired_only_fictional_test_permissions':True})
        # Resume against verified recovered copy; old cookie must fail.
        env['CROWNOPS_DB']='/data/private/recovered.sqlite3';envfile.write_text('\n'.join(k+'='+v for k,v in env.items())+'\n')
        with lockfile.open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);start(envfile,web+'-recovered')
            events.append(json.loads(docker('exec',web+'-recovered','python','/opt/g5/http_probe.py','resumed').stdout))
            stop(web+'-recovered')
    (E/'container-results.json').write_text(json.dumps({'status':'PASS','events':events,'real_Railway':'NOT TESTED','actual_offsite':'NOT TESTED','scope':'Docker-managed disposable volume, no host/business bind mounts; network=none runtime; fictional accounts/keys only','lock_limit':'Trusted cooperative harness lock plus stopped-container/mount checks, not a Railway-native volume lock or arbitrary-admin protection'},indent=2)+'\n')
try:main()
except Exception as error:
    (E/'container-results.json').write_text(json.dumps({'status':'FAIL','reason':str(error),'events':events,'real_Railway':'NOT TESTED'},indent=2)+'\n');raise
finally:
    for name in containers:docker('rm','-f',name,ok=False)
    for volume in volumes:docker('volume','rm',volume,ok=False)
