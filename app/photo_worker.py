"""Convert a single photo. Called in a separate, resource-limited process."""
import sys
import warnings

def convert(source,target):
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(15,15))
    resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024*1024,2*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS=24_000_000
    warnings.simplefilter('error',Image.DecompressionBombWarning)
    with Image.open(source,formats=('JPEG','PNG','WEBP')) as image:
        if image.width*image.height>24_000_000 or getattr(image,'n_frames',1)!=1:raise ValueError('Unsupported image dimensions or animation')
        image.verify()
    with Image.open(source,formats=('JPEG','PNG','WEBP')) as image:
        image.load();oriented=ImageOps.exif_transpose(image)
        oriented.thumbnail((2048,2048),Image.Resampling.LANCZOS)
        # A fresh canvas prevents EXIF, GPS, comments, profiles and appended bytes being retained.
        clean=Image.new('RGB',oriented.size,'white')
        if oriented.mode in ('RGBA','LA') or 'transparency' in oriented.info:
            pixels=oriented.convert('RGBA');clean.paste(pixels,mask=pixels.getchannel('A'))
        else:clean.paste(oriented.convert('RGB'))
        clean.save(target,format='JPEG',quality=85,optimize=False,exif=b'',icc_profile=None)

if __name__=='__main__':
    try:convert(sys.argv[1],sys.argv[2])
    except Exception:sys.exit(1)
