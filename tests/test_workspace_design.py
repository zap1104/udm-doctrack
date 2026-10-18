"""Shared design assets must reach each existing page family without replacing forms."""
import pytest

from tests.test_template_accessibility import RenderedHTML


@pytest.mark.django_db
@pytest.mark.parametrize('url', [
    '/', '/tracking/', '/tracking/new/', '/documents/', '/documents/upload/',
    '/search/', '/tracking/reports/', '/documents/reports/', '/tracking/external/',
    '/documents/external/', '/administration/', '/notifications/', '/accounts/profile/',
])
def test_shared_design_reaches_all_workspaces(client, users, url):
    client.force_login(users['admin'])
    response = client.get(url)
    assert response.status_code == 200
    html = response.content.decode()
    for asset in ['css/app-theme.css', 'css/workspace-controls.css', 'js/workspace-controls.js', 'js/workspace-forms.js']:
        assert html.count(asset) == 1
    assert 'IBMPlexSans-Regular.woff2' in html
    page = RenderedHTML(html)
    search = page.by_id('topbar-search-input')
    assert search['attrs']['name'] == 'q'
    assert search['parent']['attrs']['method'] == 'get'
    assert 'hidden' not in search['attrs']
    assert any('data-theme-toggle' in node['attrs'] for node in page.nodes)
    if url != '/':
        assert 'js/dashboard-tabs.js' not in html


@pytest.mark.django_db
def test_sign_in_gets_typography_and_native_account_fields(client):
    response = client.get('/accounts/login/')
    assert response.status_code == 200
    html = response.content.decode()
    assert 'css/app-theme.css' in html and 'js/workspace-forms.js' in html
    page = RenderedHTML(html)
    assert page.field('password')['attrs']['type'] == 'password'
    assert page.label(page.field('username'))['text'].strip().startswith('Username')


@pytest.mark.django_db
def test_builtin_admin_uses_shared_font_and_its_native_design_tokens(client, users):
    from django.urls import reverse

    client.force_login(users['admin'])
    response = client.get(reverse('admin:index'))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'css/app-theme.css' in html and 'css/admin-theme.css' in html
    assert 'css/workspace-controls.css' not in html
