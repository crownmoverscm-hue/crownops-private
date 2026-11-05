"""White-label delivery of pages, scripts, styles, manifest and icons (COM-2c).

The static client was written for Crown Movers (about 100 brand strings in 20+ files
and Crown artwork in the icons). Rather than editing each literal, every static text
asset is served through one branding layer:

* The original Crown installation (legacy identity) is untouched: no rewriting at all,
  so its pages and scripts are byte-identical.
* Every other installation receives the same assets with Crown's name, short name,
  phone, email and tel: links replaced by its own company profile, escaped for the
  format being served (HTML, JavaScript/CSS string literals, or JSON), and icons
  generated from its uploaded logo or its initials. "CrownOps" (the product) is kept.

Rewritten responses carry no ETag/Last-Modified and use Cache-Control: no-cache, and
conditional request headers are dropped, so a browser never keeps another identity.
"""
import base64
import html
import json
import re
from contextlib import closing
from io import BytesIO

from fastapi.responses import Response

from .company_profile import LEGACY_MODE, LOGO_KEY, company_profile
from .platform_common import settings
from .store import connect

PAGES = {'/', '/portal', '/request', '/approval', '/participate', '/invoice-access', '/manifest.webmanifest'}
TEXT_SUFFIXES = ('.js', '.css', '.html', '.webmanifest')
ICON_SIZES = {'app-192.png': 192, 'app-512.png': 512, 'app-maskable-512.png': 512, 'apple-touch-180.png': 180, 'favicon-32.png': 32}
STANDALONE_CROWN = re.compile(r'\bCrown\b(?!Ops)')


def _escape(value, fmt):
    if fmt == 'html':
        return html.escape(value, quote=True)
    if fmt == 'json':
        return json.dumps(value)[1:-1]
    # JavaScript/CSS: safe inside '…', "…" and `…` literals (profile names cannot contain ` $ { } \ < >).
    return value.replace("'", "\\'").replace('"', '\\"')


def replacements(company):
    ready = company.get('ready')
    name = company['name'] if ready else 'Moving company'
    short = company['short_name'] if ready else 'the moving company'
    phone = company['phone'] if ready else ''
    tel = company['phone_tel'] if ready else ''
    email = company['email'] if ready else ''
    return [('info@crownmovers.ca', email), ('crownmovers.cm@gmail.com', email), ('CROWN MOVERS', name.upper()),
            ('Crown Movers', name), ('(514) 606-4030', phone), ('514-606-4030', phone), ('+15146064030', tel),
            ('5146064030', re.sub(r'\D', '', tel)), (STANDALONE_CROWN, short)]


def rewrite(text, fmt, company):
    for pattern, value in replacements(company):
        value = _escape(value, fmt)
        text = pattern.sub(lambda _m: value, text) if hasattr(pattern, 'sub') else text.replace(pattern, value)
    return text


def _format_for(path, content_type):
    if path.endswith('.webmanifest') or 'manifest+json' in content_type:
        return 'json'
    if path.endswith(('.js', '.css')) or 'javascript' in content_type or 'text/css' in content_type:
        return 'js'
    return 'html'


def brand_icon(db, company, size):
    """A square PNG from the uploaded logo, or the company initials on its brand colour."""
    from PIL import Image, ImageDraw, ImageFont
    color = company.get('brand_color') or '#161616'
    canvas = Image.new('RGBA', (size, size), color)
    logo = settings(db, LOGO_KEY)
    if logo:
        mark = Image.open(BytesIO(base64.b64decode(logo['png_b64']))).convert('RGBA')
        mark.thumbnail((int(size * 0.8), int(size * 0.8)))
        plate = Image.new('RGBA', (size, size), '#ffffff')
        plate.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        canvas = plate
    else:
        words = [w for w in re.split(r'\s+', company['name'] if company.get('ready') else 'CrownOps') if w]
        initials = ''.join(w[0] for w in words[:2]).upper() or 'C'
        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.load_default(size=max(10, int(size * 0.42)))
        except TypeError:  # older Pillow without scalable default font
            font = ImageFont.load_default()
        box = draw.textbbox((0, 0), initials, font=font)
        draw.text(((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]), initials, fill='#ffffff', font=font)
    out = BytesIO(); canvas.save(out, 'PNG')
    return out.getvalue()


NO_CACHE = {'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'}


def register_branding(app, path):
    @app.middleware('http')
    async def white_label(request, call_next):
        target = request.url.path
        icon = target == '/static/icon.svg' or (target.startswith('/static/icons/') and target.rsplit('/', 1)[-1] in ICON_SIZES)
        text = target in PAGES or (target.startswith('/static/') and target.endswith(TEXT_SUFFIXES))
        if request.method not in ('GET', 'HEAD') or not (icon or text):
            return await call_next(request)
        with closing(connect(path)) as db:
            company = company_profile(db)
            if company['mode'] == LEGACY_MODE and not company['configured']:
                return await call_next(request)  # the original installation is served unchanged
            if icon:
                size = 512 if target.endswith('.svg') else ICON_SIZES[target.rsplit('/', 1)[-1]]
                png = brand_icon(db, company, size)
        if icon:
            if target.endswith('.svg'):
                data = base64.b64encode(png).decode()
                svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {size} {size}">'
                       f'<image width="{size}" height="{size}" xlink:href="data:image/png;base64,{data}"/></svg>')
                return Response(svg, media_type='image/svg+xml', headers=NO_CACHE)
            return Response(png, media_type='image/png', headers=NO_CACHE)
        request.scope['headers'] = [(k, v) for k, v in request.scope['headers'] if k not in (b'if-none-match', b'if-modified-since')]
        response = await call_next(request)
        if response.status_code != 200:
            return response
        body = b''.join([chunk async for chunk in response.body_iterator])
        content_type = response.headers.get('content-type', '')
        try:
            branded = rewrite(body.decode('utf-8'), _format_for(target, content_type), company).encode('utf-8')
        except UnicodeDecodeError:
            branded = body
        headers = {k: v for k, v in response.headers.items() if k.lower() not in ('content-length', 'etag', 'last-modified', 'cache-control')}
        headers['Cache-Control'] = 'no-cache'
        return Response(branded, status_code=200, headers=headers)
