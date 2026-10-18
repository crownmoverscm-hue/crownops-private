"""Convert a single photo. Called in a separate, resource-limited process."""
import sys
import warnings

def convert(source,target):
    import resource
    resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(25,25))
    resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024*1024,2*1024*1024))
    resource.setrlimit(resource.RLIMIT_NOFILE,(32,32))
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
    register_heif_opener()
    formats=('JPEG','PNG','WEBP','HEIF')
    Image.MAX_IMAGE_PIXELS=64_000_000
    warnings.simplefilter('error',Image.DecompressionBombWarning)
    with Image.open(source,formats=formats) as image:
        if image.width*image.height>64_000_000:raise ValueError('Photo exceeds 64 megapixels')
        if image.format!='HEIF' and getattr(image,'n_frames',1)!=1:raise ValueError('Unsupported animation')
        image.verify()
    with Image.open(source,formats=formats) as image:
        image.draft('RGB',(2048,2048))
        image.thumbnail((2048,2048),Image.Resampling.LANCZOS)
        oriented=ImageOps.exif_transpose(image)
        # A fresh canvas prevents EXIF, GPS, comments, profiles and appended bytes being retained.
        clean=Image.new('RGB',oriented.size,'white')
        if oriented.mode in ('RGBA','LA') or 'transparency' in oriented.info:
            pixels=oriented.convert('RGBA');clean.paste(pixels,mask=pixels.getchannel('A'))
        else:clean.paste(oriented.convert('RGB'))
        clean.save(target,format='JPEG',quality=85,optimize=False,exif=b'',icc_profile=None)

if __name__=='__main__':
    try:convert(sys.argv[1],sys.argv[2])
    except Exception:sys.exit(1)
