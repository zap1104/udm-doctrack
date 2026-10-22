"""Production static manifests must be able to serve the same local viewers."""

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import RequestFactory


def test_preview_assets_collect_and_template_renders_with_production_manifest(settings, tmp_path):
    settings.DEBUG = False
    settings.STATIC_ROOT = tmp_path / 'static'
    settings.STORAGES = {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'},
    }
    call_command('collectstatic', interactive=False, verbosity=0)
    request = RequestFactory().get('/files/preview/tracking/1/')
    request.user = AnonymousUser()
    html = render_to_string('core/file_preview.html', {
        'file': SimpleNamespace(original_name='proof.pdf', version_number=1), 'preview_kind': 'pdf',
        'version_number': 1, 'current_number': 1, 'content_url': '/files/content/tracking/1/?token=example',
        'download_url': '/files/download/tracking/1/?token=example', 'back_url': '/tracking/1/',
        'frame_url': '/files/preview-frame/',
    }, request=request)
    assert 'data-vendor-root="/static/vendor/file-preview/"' in html
    assert 'file-preview/pdfjs-dist/web/pdf_viewer.' in html
