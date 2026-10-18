"""Compact screen copy preserves document identity, actions, and optional guides."""
from html import escape

import pytest

from tests.test_filter_agreement import traffic  # noqa: F401 - shared routing fixture
from tests.test_template_accessibility import RenderedHTML


@pytest.mark.django_db
def test_topbar_omits_redundant_location_without_removing_controls(client, users):
    client.force_login(users['admin'])
    html = client.get('/').content.decode()
    assert 'class="topbar-location"' not in html
    for hook in ['topbar-search-input', 'data-theme-toggle', 'data-bs-toggle="dropdown"']:
        assert hook in html


@pytest.mark.django_db
def test_tracking_groups_full_subject_and_identifier_and_keeps_receipt_actions(client, users, traffic):  # noqa: F811
    client.force_login(users['sup'])
    records = list(client.get('/tracking/').context['records'])
    assert records
    record = records[0]
    subject = 'Request to inspect and repair the electrical installation and ventilation equipment in the main university building'
    type(record).objects.filter(pk=record.pk).update(subject=subject)
    response = client.get('/tracking/')
    html = response.content.decode()
    assert escape(subject) in html
    page = RenderedHTML(html)
    document_link = next(node for node in page.nodes if 'record-subject' in node['attrs'].get('class', '').split() and node['text'] == subject)
    cell = document_link['parent']
    assert cell['tag'] == 'td'
    assert record.display_tracking_number in cell['text']
    headers = [node['text'].strip() for node in page.nodes if node['tag'] == 'th']
    assert headers == ['Select for receipt', 'Document', 'Received by', 'Awaiting receipt', 'Updated', 'Status', 'Action']
    assert 'record-action' in html and 'bulk-confirm-custody' in html
    assert page.by_id('bulk-confirm-custody')['attrs']['required'] is None


@pytest.mark.django_db
@pytest.mark.parametrize('url', ['/', '/tracking/reports/'])
def test_turnaround_explanations_start_in_a_closed_guide_and_keep_local_calculation_schedule(client, users, url):
    client.force_login(users['admin'])
    page = RenderedHTML(client.get(url).content.decode())
    guide = next(node for node in page.nodes if node['attrs'].get('data-help-title') == 'Turnaround guide')
    assert guide['tag'] == 'details' and 'open' not in guide['attrs']
    assert 'Office schedule:' in guide['text'] and 'How the average works:' in guide['text']
    stored = next(node for node in page.nodes if 'data-turnaround-guide' in node['attrs'])
    assert stored['tag'] == 'template' and 'Office schedule:' in stored['text']
