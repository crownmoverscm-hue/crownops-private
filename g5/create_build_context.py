"""Hash-checked minimal tar input; no Docker invocation or network access.

Pins are the existing qualified source, not mutable checkout content. Deliberately
rejects a redacted review mirror; actual image builds require exact Git source.
"""
import argparse,hashlib,io,json,os,stat,tarfile,tempfile
from pathlib import Path

def digest(data):return hashlib.sha256(data).hexdigest()

def prepare(root,out,pins):
    root=Path(root);out=Path(out)
    if root.is_symlink():raise ValueError('Symlink checkout refused')
    root=root.resolve(strict=True)
    if not out.is_absolute() or any(p.is_symlink() for p in (out,*out.parents)):raise ValueError('Absolute nonsymlink output required')
    if out.exists():raise FileExistsError('Build input output already exists')
    entries={}
    for relative,wanted in pins['source_sha256'].items():
        p=root/relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts:raise ValueError('Unsafe context path')
        if any(x.is_symlink() for x in (p,*p.parents)) or not p.is_file():raise ValueError('Unsafe source file')
        b=p.read_bytes()
        if digest(b)!=wanted:raise ValueError('Qualified source hash mismatch: '+relative)
        entries[relative]=b
    wheel_dir=root/'build/wheelhouse'
    if any(p.is_symlink() for p in (wheel_dir,*wheel_dir.parents)) or not wheel_dir.is_dir():raise ValueError('Approved nonsymlink wheelhouse required')
    actual={p.name for p in wheel_dir.iterdir()}
    if actual!=set(pins['wheel_sha256']):raise ValueError('Unexpected/missing wheel distribution')
    for name,wanted in pins['wheel_sha256'].items():
        p=wheel_dir/name
        if p.is_symlink() or not p.is_file():raise ValueError('Unsafe wheel distribution')
        b=p.read_bytes()
        if digest(b)!=wanted:raise ValueError('Wheel hash mismatch')
        entries['build/wheelhouse/'+name]=b
    # This host-side packaging policy is not copied into the image's g5 payload.
    # Only the hash-pinned original g5 files appear in the context.
    ignore=root/'.dockerignore'
    if ignore.is_symlink() or not ignore.is_file():raise ValueError('Missing .dockerignore')
    entries['.dockerignore']=ignore.read_bytes()
    out.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.context-',dir=out.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            with tarfile.open(fileobj=f,mode='w') as tar:
                for name,b in sorted(entries.items()):
                    info=tarfile.TarInfo(name);info.size=len(b);info.mode=0o644
                    info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
                    tar.addfile(info,io.BytesIO(b))
            f.flush();os.fsync(f.fileno())
        os.link(tmp,out)  # Refuse a concurrently created destination too.
    finally:
        os.unlink(tmp)
    return {'source_files':len(pins['source_sha256']),'wheels':len(pins['wheel_sha256']),
            'context_entries':len(entries),'tar_bytes':out.stat().st_size,
            'tar_sha256':digest(out.read_bytes()),'docker_executed':False}

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--root',default='.')
    a.add_argument('--output',required=True);args=a.parse_args()
    pins=json.loads((Path(__file__).parent/'CONTEXT-PINS.json').read_text())
    print(json.dumps(prepare(args.root,args.output,pins),sort_keys=True))
