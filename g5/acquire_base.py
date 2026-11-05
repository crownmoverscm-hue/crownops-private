"""Acquire ONLY the owner-approved public image; reject unapproved redirects.
No Docker credentials/login, extra images, upload or provider activity.
"""
import gzip,hashlib,json,os,tarfile,urllib.parse,urllib.request,urllib.error
from pathlib import Path

DIGEST='sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9'
ALLOWED={'auth.docker.io','registry-1.docker.io','production.cloudfront.docker.com'}
E=Path('evidence/g5');E.mkdir(parents=True,exist_ok=True)
W=Path('build/approved-base');W.mkdir(parents=True,exist_ok=True)
events=[]

class Blocked(Exception):pass

def check(url):
    p=urllib.parse.urlsplit(url)
    if p.scheme!='https' or p.hostname not in ALLOWED or p.port not in (None,443) or p.username or p.password:
        # Host only: do not log signed redirect URL/query or auth headers.
        raise Blocked('UNAPPROVED_DESTINATION:'+str(p.hostname))

class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        check(newurl)
        events.append({'event':'approved_redirect','host':urllib.parse.urlsplit(newurl).hostname})
        # Never forward registry authorization to a CDN or another host.
        return urllib.request.Request(newurl,headers={'User-Agent':'CrownOps-G5-base-check'})

opener=urllib.request.build_opener(Redirect())
def get(url,headers=None,limit=300*1024*1024):
    check(url)
    events.append({'event':'GET','host':urllib.parse.urlsplit(url).hostname})
    with opener.open(urllib.request.Request(url,headers=headers or {}),timeout=90) as r:
        b=r.read(limit+1)
    if len(b)>limit:raise Blocked('RESPONSE_SIZE_LIMIT')
    return b

def sha(b):return hashlib.sha256(b).hexdigest()

def main():
    # Public anonymous token is transient memory only, never persisted/reported.
    auth=json.loads(get('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/python:pull',limit=1024*1024))
    token=auth.get('token') or auth.get('access_token')
    if not token:raise Blocked('ANONYMOUS_AUTH_UNAVAILABLE')
    headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.docker.distribution.manifest.v2+json'}
    prefix='https://registry-1.docker.io/v2/library/python/'
    raw=get(prefix+'manifests/'+DIGEST,headers,limit=1024*1024)
    if 'sha256:'+sha(raw)!=DIGEST:raise Blocked('MANIFEST_DIGEST_MISMATCH')
    manifest=json.loads(raw)
    if manifest.get('schemaVersion')!=2 or 'layers' not in manifest:raise Blocked('UNEXPECTED_MANIFEST')
    (E/'approved-base-manifest.json').write_bytes(raw)
    config=get(prefix+'blobs/'+manifest['config']['digest'],headers,limit=2*1024*1024)
    if 'sha256:'+sha(config)!=manifest['config']['digest']:raise Blocked('CONFIG_DIGEST_MISMATCH')
    obj=json.loads(config)
    if obj.get('architecture')!='amd64' or obj.get('os')!='linux' or 'PYTHON_VERSION=3.13.18' not in obj.get('config',{}).get('Env',[]):
        raise Blocked('PLATFORM_OR_PYTHON_VERSION_MISMATCH')
    config_name=sha(config)+'.json';(W/config_name).write_bytes(config)
    paths=[];records=[]
    for i,layer in enumerate(manifest['layers']):
        if not layer.get('mediaType','').endswith(('gzip','+gzip')):raise Blocked('UNEXPECTED_LAYER_FORMAT')
        b=get(prefix+'blobs/'+layer['digest'],headers,limit=layer['size'])
        if len(b)!=layer['size'] or 'sha256:'+sha(b)!=layer['digest']:raise Blocked('LAYER_DIGEST_OR_SIZE_MISMATCH')
        plain=gzip.decompress(b)
        if 'sha256:'+sha(plain)!=obj['rootfs']['diff_ids'][i]:raise Blocked('LAYER_DIFFID_MISMATCH')
        name=layer['digest'][7:]+'/layer.tar';p=W/name;p.parent.mkdir(exist_ok=True);p.write_bytes(plain);paths.append(name)
        records.append({'digest':layer['digest'],'bytes':len(b),'diff_id':obj['rootfs']['diff_ids'][i]})
    local_manifest=[{'Config':config_name,'RepoTags':['crownops-approved-python:3.13.18'],'Layers':paths}]
    (W/'manifest.json').write_text(json.dumps(local_manifest))
    archive=Path('build/approved-python.tar')
    with tarfile.open(archive,'w') as tar:
        for name in [config_name,'manifest.json',*paths]:tar.add(W/name,arcname=name)
    report={'status':'ACQUIRED','base_manifest_digest':DIGEST,'local_image_config_digest':manifest['config']['digest'],'platform':'linux/amd64','python':'3.13.18','layers':records,'requests':events,'extra_images':0,'credentials_used':'anonymous public token memory only'}
    (E/'base-acquisition.json').write_text(json.dumps(report,indent=2)+'\n')
    print('APPROVED_BASE_ACQUIRED '+DIGEST)

if __name__=='__main__':
    try:main()
    except Exception as error:
        reason=str(error) if isinstance(error,Blocked) else ('HTTP_'+str(error.code) if isinstance(error,urllib.error.HTTPError) else type(error).__name__)
        (E/'base-acquisition.json').write_text(json.dumps({'status':'BLOCKED','reason':reason,'approved_digest':DIGEST,'requests':events,'build_executed':False,'container_tests_executed':False,'no_alternate_destination_attempted':True},indent=2)+'\n')
        raise SystemExit('STOP: approved base acquisition blocked: '+reason)
