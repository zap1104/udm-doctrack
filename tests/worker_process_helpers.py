"""Trusted task and runner for the opt-in disposable-process queue audit."""
import json
import os
import time
from pathlib import Path


def interrupted_extraction(document_id, marker, user_id, generation):
    """Pause the first attempt so the test can interrupt its actual worker."""
    path = Path(marker)
    if not path.exists():
        path.write_text(json.dumps({'pid': os.getpid(), 'parent': os.getppid()}))
        time.sleep(90)
    from apps.documents.tasks import extract_document_task
    return extract_document_task(document_id, user_id=user_id, generation=generation)


if __name__ == '__main__':
    import sys

    import django
    django.setup()
    from django_q.cluster import Cluster

    stop = Path(sys.argv[1])
    cluster = Cluster()
    try:
        cluster.start()
        while not stop.exists():
            time.sleep(.1)
    finally:
        cluster.stop()
