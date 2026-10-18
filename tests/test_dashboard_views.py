"""Dashboard tabs group the existing content without changing page features."""
from html.parser import HTMLParser

import pytest


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.panels = {}
        self.owner = {}
        self.tabs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        panel = attrs.get('data-dashboard-panel')
        current = panel or (self.stack[-1][1] if self.stack else None)
        if panel:
            self.panels[panel] = attrs
        if attrs.get('id'):
            self.owner[attrs['id']] = current
        if 'data-dashboard-tab' in attrs:
            self.tabs[attrs['id']] = attrs
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append((tag, current))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['admin', 'med_admin', 'sup', 'viewer'])
def test_tabs_preserve_queues_analytics_and_no_script_access(client, users, role):
    client.force_login(users[role])
    response = client.get('/')
    assert response.status_code == 200
    parser = DashboardParser()
    html = response.content.decode()
    parser.feed(html)
    assert set(parser.panels) == {'overview', 'statistics'}
    assert all('hidden' not in attrs for attrs in parser.panels.values())
    for name, attrs in parser.panels.items():
        tab = parser.tabs[attrs['aria-labelledby']]
        assert tab['aria-controls'] == attrs['id']
        assert tab['data-dashboard-tab'] == name
    assert parser.owner['action-centre-queue'] == 'overview'
    assert parser.owner['dashboard-filing-title'] == 'overview'
    assert parser.owner['side-tracking-title'] == 'statistics'
    assert parser.owner['side-repository-title'] == 'statistics'
    assert parser.owner['turnaround-panel'] == 'statistics'
    assert parser.owner['dashboard-memo'] is None
    assert 'css/dashboard.css' in html
    assert 'js/dashboard-tabs.js' in html
    assert 'css/workspace-controls.css' in html
    assert 'js/workspace-controls.js' in html
    tracking_html = client.get('/tracking/').content.decode()
    assert 'css/dashboard.css' not in tracking_html
    assert 'js/dashboard-tabs.js' not in tracking_html
    assert 'css/workspace-controls.css' in tracking_html
    assert 'js/workspace-controls.js' in tracking_html
    assert 'workspace-search-trigger' in tracking_html
    assert 'workspace-theme-track' in tracking_html


@pytest.mark.django_db
def test_dashboard_controls_keep_native_search_and_existing_theme_handler(client, users):
    from django.urls import reverse

    from tests.test_template_accessibility import RenderedHTML

    client.force_login(users['admin'])
    page = RenderedHTML(client.get('/', {'q': 'office & records'}).content.decode())
    search = page.by_id('topbar-search-input')
    assert search['attrs']['type'] == 'search'
    assert search['attrs']['name'] == 'q'
    assert search['attrs']['value'] == 'office & records'
    assert 'hidden' not in search['attrs'] and 'tabindex' not in search['attrs']
    assert search['attrs'].get('inputmode') != 'none'
    assert search['parent']['attrs']['method'] == 'get'
    assert search['parent']['attrs']['action'] == reverse('search:index')
    assert page.label(search)['text'] == 'Search the repository'
    trigger = next(node for node in page.nodes if 'workspace-search-trigger' in node['attrs'].get('class', '').split())
    assert trigger['attrs']['type'] == 'button' and 'hidden' in trigger['attrs']
    assert trigger['attrs']['aria-controls'] == search['attrs']['id']
    theme = next(node for node in page.nodes if 'data-theme-toggle' in node['attrs'])
    assert theme['tag'] == 'button' and theme['attrs']['type'] == 'button'
    assert theme['attrs']['aria-label'] == 'Switch to dark theme'


@pytest.mark.django_db
def test_turnaround_averages_stay_visible_and_optional_details_are_accessible(client, users):
    from tests.test_template_accessibility import RenderedHTML

    client.force_login(users['admin'])
    page = RenderedHTML(client.get('/').content.decode())
    explanation = next(node for node in page.nodes if 'dashboard-time-explanation' in node['attrs'].get('class', '').split())
    assert explanation['tag'] == 'details' and 'open' not in explanation['attrs']
    assert 'How time is counted' in explanation['text']
    assert 'Elapsed time' in explanation['text'] and 'Office schedule:' in explanation['text']
    assert 'Averages use exact seconds' in explanation['text']
    for node in page.nodes:
        if 'dashboard-turnaround-averages' not in node['attrs'].get('class', '').split():
            continue
        ancestor = node['parent']
        while ancestor:
            assert ancestor['tag'] != 'details'
            ancestor = ancestor['parent']


def test_dashboard_disclosure_does_not_change_report_stage_presentation():
    from django.template.loader import render_to_string

    from tests.test_template_accessibility import RenderedHTML

    stage = {
        'key': 'receipt', 'label': 'Pending Receipt', 'samples': 2, 'noun': 'endorsement',
        'average_seconds': 3600, 'average_calendar_seconds': 7200,
        'average_outside_seconds': 3600, 'total_office_seconds': 7200,
        'zero_working_time': 1,
        'fastest': {'office_seconds': 3600, 'calendar_seconds': 7200, 'url': '/tracking/1/', 'tracking_number': 'TR-ONE'},
        'slowest': {'office_seconds': 7200, 'calendar_seconds': 10800, 'url': '/tracking/2/', 'tracking_number': 'TR-TWO'},
    }
    context = {'stage': stage, 'id_prefix': 'test', 'turnaround': {'is_current': True}}
    report = render_to_string('core/_turnaround_stage.html', context)
    assert 'dashboard-stage-details' not in report
    compact = render_to_string('core/_turnaround_stage.html', {**context, 'dashboard_compact': True})
    page = RenderedHTML(compact)
    details = next(node for node in page.nodes if node['tag'] == 'details')
    assert 'open' not in details['attrs']
    assert 'Fastest within office hours' in details['text']
    assert 'Slowest within office hours' in details['text']
    assert 'TR-ONE' in details['text'] and 'TR-TWO' in details['text']
    assert '1 of 2' in details['text'] and 'View calculation' in details['text']
    trigger = next(node for node in page.nodes if node['tag'] == 'summary')
    assert trigger['attrs']['aria-label'] == 'Details and calculation for Pending Receipt'
