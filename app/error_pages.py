"""Friendly browser recovery without changing the JSON API error contract."""
from pathlib import Path
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException


def register_error_pages(app, db_path=None):
    """db_path is None for the locked Core v1 profile, which keeps the original page unchanged."""
    template = (Path(__file__).parent / 'static' / 'link_error.html').read_text()

    @app.exception_handler(HTTPException)
    async def browser_error(request, exc):
        path = request.url.path
        browser = 'text/html' in request.headers.get('accept', '').lower() or request.headers.get('sec-fetch-dest') == 'document'
        if (exc.status_code not in (404, 410) or request.method not in ('GET', 'HEAD')
                or path == '/api' or path.startswith(('/api/', '/static/')) or not browser):
            return await http_exception_handler(request, exc)
        language = request.query_params.get('lang', '').lower()
        if language not in ('en', 'fr'):
            language = 'fr' if request.headers.get('accept-language', '').lower().startswith('fr') else 'en'
        fr = language == 'fr'
        from html import escape
        from .company_profile import LEGACY_PROFILE, profile_for_path
        company = profile_for_path(db_path) if db_path else {**LEGACY_PROFILE, 'mode': 'legacy-crown', 'ready': True}
        legacy = company['mode'] == 'legacy-crown'
        name = company['name'] if company['ready'] else 'CrownOps'
        tel = company['phone_tel'] if company['ready'] else ''
        values = {
            'LANG': language,
            'TITLE': 'Retrouvons votre lien' if fr else 'Let’s get you to the right place',
            'MESSAGE': ('Ce lien est peut-être incomplet, expiré ou n’est plus disponible. Appelez Crown Movers au (514) 606-4030 pour obtenir de l’aide ou un nouveau lien.' if fr else 'This link may be incomplete, expired or no longer available. Call Crown Movers at (514) 606-4030 for help or a new link.'
            ) if legacy else (('Ce lien est peut-être incomplet, expiré ou n’est plus disponible. Communiquez avec '+escape(name)+(' au '+escape(company['phone']) if tel else '')+' pour obtenir de l’aide ou un nouveau lien.') if fr else
                ('This link may be incomplete, expired or no longer available. Contact '+escape(name)+(' at '+escape(company['phone']) if tel else '')+' for help or a new link.')),
            'CALL': 'Appeler '+escape(name) if fr else 'Call '+escape(name),
            'HOME': 'Connexion de l’équipe' if fr else 'Team sign-in',
            'TIP': 'Pour votre soumission ou facture, utilisez le lien privé dans le plus récent courriel de '+escape(name)+'.' if fr else 'For your quote or invoice, use the private link in your latest '+escape(name)+' email.',
            'COMPANY': escape(name),
            'OTHER': 'English' if fr else 'Français',
            'OTHER_LANG': 'en' if fr else 'fr',
            'STATUS': str(exc.status_code),
        }
        values['CALL_LINK'] = ('<a href="tel:'+escape(tel, quote=True)+'">'+values['CALL']+'</a>') if tel else ''
        html = template
        for key, value in values.items():
            html = html.replace('__' + key + '__', value)
        # Never reflect the requested URL, token, exception detail or client data.
        return HTMLResponse(html, status_code=exc.status_code,
                            headers={'Cache-Control': 'no-store', 'Vary': 'Accept, Accept-Language',
                                     'Referrer-Policy': 'no-referrer'})
