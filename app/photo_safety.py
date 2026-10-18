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

READY_STATUSES=('Clean','Photo checked','Video checked')
PHOTO_FILE_MB=30
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
    videos=photo_mode() and bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
    return {'enabled':available,'mode':mode,'accept':('image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif'+(',video/mp4,video/quicktime,video/webm,.mov,.mp4,.webm' if videos else '')) if photo_mode() else 'image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime,video/webm',
        'max_file_mb':100 if videos else PHOTO_FILE_MB if photo_mode() else int(os.getenv('CROWNOPS_MAX_UPLOAD_MB','100')),'max_photo_mb':PHOTO_FILE_MB,'max_video_mb':100 if videos else 0,'videos_enabled':videos,'max_batch':10,
        'message':'Add photos, including iPhone HEIC and high-resolution JPEGs (up to 30 MB / 64 MP). Short MP4, MOV and WebM videos are supported up to 100 MB / 3 minutes. Larger collections can use a shared folder link. Files stay private and are processed before viewing.' if available and photo_mode() and videos else
            'Add JPEG, PNG, WebP or iPhone HEIC photos up to 30 MB / 64 MP, or paste a shared photo/video folder link. Photos stay private and are resized before viewing.' if available and photo_mode() else
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
        result=subprocess.run([sys.executable,'-I',str(worker),str(source),str(target)],capture_output=True,timeout=45,check=False,env=child_env)
    except (OSError,subprocess.TimeoutExpired):
        target.unlink(missing_ok=True)
        raise HTTPException(422,'This photo took too long to process. Try again or share a photo-folder link.')
    if result.returncode or not target.is_file() or target.stat().st_size>2*1024*1024:
        target.unlink(missing_ok=True)
        raise HTTPException(422,'This photo could not be opened. We accept JPEG, PNG, WebP and HEIC up to 30 MB / 64 MP. Try the original photo or paste a shared folder link.')
    target.chmod(0o600)
    return {'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}

def process_video(source,target):
    import json
    env={k:v for k,v in os.environ.items() if k in ('PATH','SYSTEMROOT','LD_LIBRARY_PATH')}
    try:
        probe=subprocess.run([shutil.which('ffprobe') or 'ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration:stream=codec_type,width,height','-of','json',str(source)],capture_output=True,timeout=15,check=True,env=env)
        details=json.loads(probe.stdout);duration=float(details.get('format',{}).get('duration',0));video=next(x for x in details.get('streams',[]) if x.get('codec_type')=='video')
        if not 0<duration<=180 or not 0<int(video.get('width',0))*int(video.get('height',0))<=34_000_000:raise ValueError()
        subprocess.run([shutil.which('ffmpeg') or 'ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-threads','1','-i',str(source),'-map','0:v:0','-map','0:a:0?','-sn','-dn','-map_metadata','-1','-vf',"scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",'-c:v','libx264','-threads','1','-preset','veryfast','-crf','28','-pix_fmt','yuv420p','-c:a','aac','-ac','1','-b:a','64k','-t','180','-fs',str(40*1024*1024),'-movflags','+faststart','-y',str(target)],capture_output=True,timeout=120,check=True,env=env)
        if not target.is_file() or target.stat().st_size<100 or target.stat().st_size>=40*1024*1024:raise ValueError()
    except (OSError,ValueError,StopIteration,subprocess.SubprocessError):
        target.unlink(missing_ok=True)
        raise HTTPException(422,'This video could not be processed. Use a short MP4, MOV or WebM (up to 100 MB / 3 minutes), or paste a Drive/video-folder link.')
    target.chmod(0o600)
    return {'size':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
