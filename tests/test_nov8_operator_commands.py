"""Exercise operator command boundaries on rolled-back, authored data only."""
import json
from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import User
from apps.documents.models import Collection, Document
from tests.auth_helpers import prepare_verified_user

pytestmark = pytest.mark.django_db


def invoke(name, *args, **options):
    output = StringIO()
    call_command(name, *args, stdout=output, stderr=output, **options)
    return output.getvalue()


@pytest.mark.parametrize('command', ['maintain_file_uploads', 'reconcile_file_retention', 'recover_extraction', 'reconcile_processing'])
@pytest.mark.django_db(transaction=True)
def test_bounded_maintenance_dry_run_and_repeated_apply(command):
    assert invoke(command, limit=1)
    assert invoke(command, limit=1, apply=True)
    assert invoke(command, limit=1, apply=True)
    assert not Document.objects.exists()


def test_purge_command_rejects_invalid_limit_and_is_repeatable():
    with pytest.raises(CommandError, match='at least 1'):
        invoke('purge_deleted_documents', limit=0)
    for _ in range(2):
        assert 'Permanently deleted: 0; failed (will retry): 0' in invoke('purge_deleted_documents', limit=1)


def test_reindex_command_updates_both_collections_and_respects_office(users):
    rows = [Document.objects.create(title=f'Authored {collection}', year=2026,
        collection=collection, office=users['med'].office, uploaded_by=users['med']) for collection in Collection.values]
    foreign = Document.objects.create(title='Foreign office', year=2026, office=users['sup'].office, uploaded_by=users['sup'])
    assert 'Reindexed 2' in invoke('reindex_documents', office='MED', batch_size=1)
    for row in rows:
        row.refresh_from_db()
        assert row.search_vector and row.index_title == row.title
    foreign.refresh_from_db()
    assert not foreign.search_vector
    assert 'Reindexed 3' in invoke('reindex_documents', batch_size=1)
    assert 'Nothing to index' in invoke('reindex_documents', office='UNKNOWN')


def test_account_creation_and_targeted_operator_recovery(users, settings):
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.EMAIL_CONFIGURED = True
    settings.SITE_BASE_URL = 'https://test.invalid'
    options = {'username': 'audit.operator', 'password': 'UniqueOperatorPassword!812', 'email': 'operator@example.test'}
    invoke('create_admin', **options)
    invoke('create_admin', **options)
    user = User.objects.get(username='audit.operator')
    assert user.is_system_admin and user.is_staff and user.is_superuser and user.must_change_password
    assert user.check_password(options['password']) and not user.email_is_verified
    assert User.objects.filter(username='audit.operator').count() == 1
    before = users['sup'].password
    settings.DEBUG = False
    with pytest.raises(CommandError, match='Production password repair'):
        invoke('fix_login', reset_password=True)
    invoke('fix_login', user=user.username, reset_password=True, password='SecondUniquePassword!973')
    user.refresh_from_db()
    users['sup'].refresh_from_db()
    assert user.check_password('SecondUniquePassword!973') and users['sup'].password == before
    invoke('recover_account_email', user=user.username, email='replacement@example.test', reason='Disposable operator recovery check')
    user.refresh_from_db()
    assert user.email == 'replacement@example.test' and not user.email_is_verified
    assert mail.outbox[-1].to == ['replacement@example.test']
    assert '/accounts/email/invitation/' in mail.outbox[-1].body


def test_readiness_is_read_only_and_refuses_simulated_delivery(users, settings):
    before = list(User.objects.values_list('pk', 'password', 'email', 'verified_email'))
    with pytest.raises(CommandError, match='missing or unverified'):
        invoke('check_otp_readiness')
    assert before == list(User.objects.values_list('pk', 'password', 'email', 'verified_email'))
    for user in users.values():
        prepare_verified_user(user)
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    with pytest.raises(CommandError, match='real email-delivery'):
        invoke('check_otp_readiness')
    settings.EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    settings.EMAIL_CONFIGURED = True
    assert 'Actual delivery is not proved' in invoke('check_otp_readiness')


def test_extension_initialization_is_repeatable_and_seed_guard_is_honest(settings):
    from django.db import connection
    invoke('init_db')
    with connection.cursor() as cursor:
        cursor.execute('SELECT extname FROM pg_extension ORDER BY extname')
        before = cursor.fetchall()
    invoke('init_db')
    with connection.cursor() as cursor:
        cursor.execute('SELECT extname FROM pg_extension ORDER BY extname')
        assert before == cursor.fetchall()
    assert {'pg_trgm', 'unaccent'}.issubset({row[0] for row in before})
    settings.ALLOW_DEMO_SEED = False
    with pytest.raises(CommandError, match='disabled'):
        invoke('seed_demo', records=0)


def test_stage_reconciliation_dry_run_changes_only_cached_summary(users, offices):
    from apps.tracking.models import Status, TrackingRecord
    from apps.tracking.services import create_draft_record, route_record
    record = create_draft_record(user=users['sup'], subject='Authored stage repair', instructions='Review')
    route_record(record, [offices['MED']], user=users['sup'])
    history = list(record.routing_steps.values())
    TrackingRecord.objects.filter(pk=record.pk).update(status=Status.IN_PROCESS)
    assert 'Would update 1' in invoke('reconcile_tracking_stages', record=record.tracking_number)
    record.refresh_from_db()
    assert record.status == Status.IN_PROCESS
    assert 'Updated 1' in invoke('reconcile_tracking_stages', record=record.tracking_number, apply=True)
    record.refresh_from_db()
    assert record.status == Status.PENDING_RECEIPT
    assert history == list(record.routing_steps.values())
    assert 'Updated 0' in invoke('reconcile_tracking_stages', record=record.tracking_number, apply=True)


def test_export_and_search_evaluation_are_scoped_and_authored(users, tmp_path):
    row = Document.objects.create(title='Unique exact authored search', year=2026,
        office=users['med'].office, uploaded_by=users['med'], reference_number='AUDIT-EXACT-ONE')
    row.rebuild_index()
    target = tmp_path / 'reviewed.jsonl'
    assert 'Wrote 0' in invoke('export_training_data', out=str(target))
    assert target.read_text() == ''
    judgments = tmp_path / 'judgments.json'
    judgments.write_text(json.dumps([{'query': 'AUDIT-EXACT-ONE', 'relevant_ids': [row.pk], 'min_relevance': 0}]))
    result = json.loads(invoke('evaluate_search', str(judgments), actor=users['med'].pk, collection='OVPA'))
    row.refresh_from_db()
    assert result and row.search_vector
    with pytest.raises(CommandError):
        invoke('evaluate_search', str(judgments), actor=999999, collection='OVPA')


def test_topic_model_commands_use_frozen_authored_evidence_and_never_autopromote(users, settings, tmp_path):
    from apps.documents.ml.models import TagModel
    from tests.ml_fixtures import make_candidate
    settings.MEDIA_ROOT = tmp_path
    settings.ENABLE_TAG_ML = True
    candidate = make_candidate(users)
    count = TagModel.objects.count()
    assert 'Candidate' in invoke('train_tag_model', candidate.snapshot_id, actor=users['admin'].pk)
    assert TagModel.objects.count() == count + 1
    assert not TagModel.objects.filter(is_active=True).exists()
    report = json.loads(invoke('evaluate_tag_model', candidate.pk, actor=users['admin'].pk))
    assert isinstance(report, dict)
    assert 'shadow' in invoke('activate_tag_model', candidate.pk, actor=users['admin'].pk, mode='shadow')
    candidate.refresh_from_db()
    assert candidate.is_active and candidate.mode == 'shadow'
    assert invoke('rollback_auto_tags', candidate.pk, actor=users['admin'].pk)
    assert invoke('rollback_auto_tags', candidate.pk, actor=users['admin'].pk, apply=True)
    assert invoke('prepare_tag_dataset', actor=users['admin'].pk, office=users['med'].office_id, collection='OVPA')
    with pytest.raises(CommandError):
        invoke('prepare_tag_dataset', actor=users['viewer'].pk, office=users['med'].office_id, collection='OVPA', apply=True)
