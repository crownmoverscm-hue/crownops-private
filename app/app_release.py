"""Content-based release identity and fresh entry assets for installed web apps."""
import hashlib
import re


def release_snapshot(root):
    app_dir = root / 'app'
    files = sorted(set(app_dir.glob('*.py')) | {p for p in (app_dir / 'static').rglob('*') if p.is_file()})
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    revision = digest.hexdigest()[:24]
    html = (app_dir / 'static' / 'index.html').read_text()
    html = html.replace('__CROWNOPS_RELEASE__', revision)
    html = re.sub(r'([?&]v=)[^"\s&<>]+', lambda m: m[1] + revision, html)
    return revision, html


def register_app_release(app, root):
    revision, html = release_snapshot(root)
    app.state.ui_revision = revision
    app.state.ui_index = html

    @app.get('/api/app-release')
    def current_release():
        return {'revision': revision, 'version': app.version}
