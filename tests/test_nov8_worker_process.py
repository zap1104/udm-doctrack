"""Actual local queue processes; all database, cache and storage are disposable."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.documents.models import Document, DocumentFile


def wait_for(predicate, *, seconds=90):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    raise AssertionError('Disposable queue worker did not reach its expected saved state.')


@pytest.mark.skipif(os.environ.get('RUN_Q2_PROCESS_AUDIT') != '1', reason='Opt-in real local django-q2 process audit')
@pytest.mark.django_db(transaction=True)
def test_real_workers_retry_duplicates_and_recheck_authority(users, settings):
    from django_q.models import OrmQ, Task
    from django_q.tasks import async_task

    assert settings.ENABLE_BACKGROUND_TASKS
    assert settings.DATABASES['default']['NAME'].startswith('test_doctrack_security_20261108')
    root = Path(settings.BASE_DIR)
    output = Path(os.environ['Q2_AUDIT_OUTPUT']).resolve()
    assert output.is_relative_to(root / '.vercel' / 'security-logic-20261108')
    output.mkdir(parents=True, exist_ok=True)
    stop, marker = output / 'stop', output / 'interrupted.json'
    assert not stop.exists() and not marker.exists(), 'Use a fresh process-audit output directory.'

    def document(title, actor, *, text=''):
        row = Document.objects.create(title=title, office=actor.office, uploaded_by=actor, year=2026,
                                      extraction_generation=1, ocr_text=text)
        DocumentFile.objects.create(document=row, uploaded_by=actor,
            original_name='audit.txt', file=SimpleUploadedFile('audit.txt', b'Queue audit original maintenance evidence.'))
        return row

    normal = document('Duplicate jobs', users['med'])
    interrupted = document('Interrupted worker', users['med'])
    denied = document('Revoked queued authority', users['sup'], text='Retained private text')
    stale = document('Superseded queued generation', users['med'], text='Newer authoritative text')
    Document.objects.filter(pk=stale.pk).update(extraction_generation=2)
    users['sup'].is_active = False
    users['sup'].save(update_fields=['is_active'])
    job_ids = [async_task('apps.documents.tasks.extract_document_task', normal.pk,
        user_id=users['med'].pk, generation=1) for _ in range(2)]
    job_ids += [async_task('apps.documents.tasks.extract_document_task', denied.pk,
        user_id=users['sup'].pk, generation=1), async_task('apps.documents.tasks.extract_document_task', stale.pk,
        user_id=users['med'].pk, generation=1)]
    job_ids += [async_task('tests.worker_process_helpers.interrupted_extraction', interrupted.pk,
        str(marker), users['med'].pk, 1)]
    errors = output / 'worker.log'
    with errors.open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-m', 'tests.worker_process_helpers', str(stop)],
            cwd=root, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_for(marker.exists)
            identity = json.loads(marker.read_text())
            assert identity['pid'] not in {os.getpid(), process.pid}
            assert identity['parent'] not in {0, os.getpid()}
            # This PID comes from the just-started task's private marker, not a
            # process-name search that could stop somebody else's worker.
            os.kill(identity['pid'], signal.SIGTERM)
            wait_for(lambda: Task.objects.filter(pk__in=job_ids, success=True).count() == len(job_ids), seconds=120)
            assert not Task.objects.filter(pk__in=job_ids, success=False).exists()
            for row in (normal, interrupted, denied, stale):
                row.refresh_from_db()
            assert normal.ocr_status == interrupted.ocr_status == 'DONE'
            assert normal.ocr_text == interrupted.ocr_text == 'Queue audit original maintenance evidence.'
            assert denied.ocr_status == 'SKIPPED' and denied.ocr_text == 'Retained private text'
            assert stale.extraction_generation == 2 and stale.ocr_text == 'Newer authoritative text'
            assert DocumentFile.objects.filter(document__in=[normal, interrupted, denied, stale]).count() == 4
            wait_for(lambda: not OrmQ.objects.exists())
            (output / 'results.json').write_text(json.dumps({'jobs': len(job_ids), 'workers': 2,
                'interrupted_worker_recovered': True, 'duplicate_jobs_completed': True,
                'revoked_actor_refused': True, 'superseded_generation_preserved': True}, indent=2))
        finally:
            stop.touch()
            process.wait(timeout=60)
    assert process.returncode == 0, errors.read_text(encoding='utf-8')[-4000:]
