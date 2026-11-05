"""UNCOMMITTED review draft. Offline hash verification and tar creation only.

No Docker, pip, network, app imports or source mutation. Not authorized for build.
Copies every original pin, the three reviewed 91cea01 runtime inputs and five
ab8c6093 files. Never trusts a redacted review mirror as exact build source.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile

MANIFEST_SHA256 = 'da05fe9431119759369d34dea0439ed146c0efd000b8ce7a341b451b3e6af010'


def read_checked(root, relative, expected=None):
    p = PurePosixPath(relative)
    if p.is_absolute() or not p.parts or any(x in ('.','..') for x in p.parts) or '/'.join(p.parts) != relative:
        raise ValueError('unsafe relative path')
    root = Path(root)
    if not root.is_absolute():
        raise ValueError('absolute source root required')
    parent = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:] + p.parts[:-1]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent); parent = new
        fd = os.open(p.parts[-1],os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 256*1024*1024:
                raise ValueError('regular bounded single-link source required')
            chunks=[]
            while data := os.read(fd,65536):chunks.append(data)
            after=os.fstat(fd)
            if any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')):
                raise ValueError('changed source')
            data=b''.join(chunks)
        finally:os.close(fd)
    finally:os.close(parent)
    if expected is not None and hashlib.sha256(data).hexdigest()!=expected:
        raise ValueError('approved hash mismatch: '+relative)
    return data


def approved_manifest():
    here=Path(__file__).absolute().parent
    return json.loads(read_checked(here,'LIFECYCLE-CONTEXT.json',MANIFEST_SHA256))


def prepare(root, output):
    m=approved_manifest()
    pins=json.loads(read_checked(root,'g5/CONTEXT-PINS.json',m['legacy_context_pins_sha256']))
    if len(pins['wheel_sha256'])!=m['wheel_count'] or m['wheel_count']!=35:
        raise ValueError('exact 35 distributions required')
    entries={}
    for group in (pins['source_sha256'],m['parent_runtime_sha256'],m['lifecycle_sha256']):
        for name,digest in group.items():
            data=read_checked(root,name,digest)
            if name in entries and entries[name]!=data:raise ValueError('conflicting original pin')
            entries[name]=data
    wheels=Path(root)/'build/wheelhouse'
    # read_checked walks this directory with NOFOLLOW before any listed file read.
    if {p.name for p in wheels.iterdir()} != set(pins['wheel_sha256']):
        raise ValueError('unexpected/missing wheel distribution')
    for name,digest in pins['wheel_sha256'].items():
        relative='build/wheelhouse/'+name
        entries[relative]=read_checked(root,relative,digest)
    output=Path(output)
    if not output.is_absolute() or output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('absolute nonsymlink output required')
    if os.path.lexists(output):raise FileExistsError('no-overwrite context')
    # Parent must already exist; no implicit directory creation or permission repair.
    fd,tmp=tempfile.mkstemp(prefix='.lifecycle-context-',dir=output.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            with tarfile.open(fileobj=stream,mode='w') as archive:
                for name,data in sorted(entries.items()):
                    info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644
                    info.uid=info.gid=info.mtime=0;info.uname=info.gname=''
                    archive.addfile(info,io.BytesIO(data))
            stream.flush();os.fsync(stream.fileno())
        os.link(tmp,output)
        dfd=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:os.unlink(tmp)
    return {'original_pins_verified':len(pins['source_sha256']),
            'lifecycle_files_verified':len(m['lifecycle_sha256']),
            'wheels_verified':35,'context_entries':len(entries),
            'canonical_dockerfile':m['canonical_dockerfile'],
            'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
            'docker_executed':False,'network_acquisition':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    try:print(json.dumps(prepare(a.root,a.output),sort_keys=True))
    except Exception:raise SystemExit('REFUSED: lifecycle context not published')
