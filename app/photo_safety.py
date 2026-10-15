"""Restricted photo processing; this mode does not claim antivirus scanning."""
from pathlib import Path
from functools import lru_cache
import base64
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from fastapi import HTTPException

READY_STATUSES=('Clean','Photo checked')
PHOTO_FILE_MB=15
HEADROOM=256*1024*1024

def photo_mode():return os.getenv('CROWNOPS_UPLOAD_MODE','').strip().lower()=='photos'

def photo_processor_ready():
    return importlib.util.find_spec('PIL') is not None and Path(__file__).with_name('photo_worker.py').is_file()

@lru_cache(maxsize=1)
def photo_processor_self_test():
    with tempfile.TemporaryDirectory(prefix='crown-photo-check-') as folder:
        source=Path(folder)/'test.png';target=Path(folder)/'test.jpg'
        source.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC'))
        process_photo(source,target)
        if not target.read_bytes().startswith(b'\xff\xd8\xff'):raise RuntimeError('Photo processing self-test failed.')
    return True

def upload_policy():
    from .platform_common import uploads_enabled
    enabled=uploads_enabled()
    mode='photos' if photo_mode() else 'scanned-files'
    if photo_mode():ready=photo_processor_ready()
    else:ready=bool(os.getenv('CROWNOPS_SCAN_COMMAND','').strip()) or os.getenv('CROWNOPS_ENV','test')!='production'
    available=enabled and ready
    return {'enabled':available,'mode':mode,'accept':'image/jpeg,image/png,image/webp' if photo_mode() else 'image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime,video/webm',
        'max_file_mb':PHOTO_FILE_MB if photo_mode() else int(os.getenv('CROWNOPS_MAX_UPLOAD_MB','100')),'max_batch':10,
        'message':'Photos are private. JPEG, PNG and WebP images are checked, resized and stored without embedded metadata.' if available and photo_mode() else
            'Private uploads are available. Files remain unavailable until their safety checks pass.' if available else
            'Photo uploads are temporarily unavailable. Contact the office to add photos to your moving file.'}

def require_room(storage,extra=0):
    if shutil.disk_usage(storage).free<HEADROOM+extra:
        raise HTTPException(507,'Photo storage is nearly full. Contact the owner to increase storage; this file was not saved.')

def process_photo(source,target):
    """Decode in a bounded child process and keep only newly encoded JPEG pixels."""
    worker=Path(__file__).with_name('photo_worker.py')
    child_env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','LD_LIBRARY_PATH')}
    try:
        result=subprocess.run([sys.executable,'-I',str(worker),str(source),str(target)],capture_output=True,timeout=25,check=False,env=child_env)
    except (OSError,subprocess.TimeoutExpired):
        target.unlink(missing_ok=True)
        raise HTTPException(422,'This photo could not be processed. Export a smaller JPEG and try again.')
    if result.returncode or not target.is_file() or target.stat().st_size>2*1024*1024:
        target.unlink(missing_ok=True)
        raise HTTPException(422,'Use a valid JPEG, PNG or WebP photo up to 15 MB and 24 megapixels. Export other formats as JPEG first.')
    target.chmod(0o600)
    return {'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
