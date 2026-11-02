"""Build-input provenance, not a claim of container-build success."""
import hashlib,json,os,subprocess
from pathlib import Path
base='442d24892d75199816451775b5a174fc12ee1c5f'
def git(*args):return subprocess.check_output(['git',*args],text=True).strip()
expected={'app/core_pilot.py','app/security.py'}
changed=set(git('diff','--name-only',base,'--','app').splitlines())
if changed!=expected:raise SystemExit('Unexpected application source delta')
sha=git('rev-parse','HEAD');rows=[]
for name in git('ls-files').splitlines():
    data=Path(name).read_bytes();rows.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
out=Path('evidence');out.mkdir(exist_ok=True)
(out/'git-source-manifest.json').write_text(json.dumps(rows,indent=2)+'\n')
canonical=json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
(out/'run.json').write_text(json.dumps({'candidate':sha,'base':base,'branch':'release/core-v1-pilot-2026-11-09','run_id':os.getenv('GITHUB_RUN_ID'),'run_url':'https://github.com/'+os.getenv('GITHUB_REPOSITORY','crownmoverscm-hue/crownops-private')+'/actions/runs/'+os.getenv('GITHUB_RUN_ID',''),'tracked_source_manifest_sha256':hashlib.sha256(canonical).hexdigest(),'application_delta':sorted(changed),'docker_image_build':'NOT EXECUTED: no approved immutable base image digest/container acquisition in this qualification','deployment':False},indent=2)+'\n')
print('SOURCE_BOUNDARY application_files=2 accepted_base='+base+' candidate='+sha)
