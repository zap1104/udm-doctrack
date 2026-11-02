"""Opt-in Chromium journeys against real Django/CSRF and the in-memory mailbox."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from django.core import mail

from tests.auth_helpers import prepare_verified_user


@pytest.mark.skipif(os.environ.get('RUN_EMAIL_OTP_BROWSER') != '1', reason='Opt-in live OTP browser verification')
@pytest.mark.django_db(transaction=True)
def test_email_otp_and_permission_browser_journeys(live_server, users, settings):
    settings.EMAIL_OTP_REQUIRED = True
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    settings.EMAIL_CONFIGURED = True
    settings.SECURE_SSL_REDIRECT = False
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
    for user in users.values():
        prepare_verified_user(user)
    node = shutil.which('node')
    assert node, 'Node is required for the opt-in browser journey'
    root = Path(__file__).resolve().parents[1]
    output = Path(os.environ['OTP_BROWSER_OUTPUT'])
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ | {'OTP_BROWSER_BASE':live_server.url, 'OTP_BROWSER_TARGET':str(users['med'].pk)}
    errors = output / 'browser-stderr.txt'
    with errors.open('w',encoding='utf-8') as error_stream:
        process = subprocess.Popen([node,str(root/'scripts/check_email_otp_browser.cjs')], cwd=root, env=env,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=error_stream,text=True,encoding='utf-8')
        complete = None
        try:
            for line in process.stdout:
                event = json.loads(line)
                if event['event'] == 'code':
                    address = users[event['user']].email
                    latest = next(message for message in reversed(mail.outbox) if message.to == [address])
                    value = re.search(r'\b(\d{6})\b', latest.body).group(1)
                    process.stdin.write(value+'\n')
                    process.stdin.flush()
                elif event['event'] == 'complete':
                    complete = event
            code = process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
    safe_errors = re.sub(r'\b\d{6}\b','[test-code-redacted]',errors.read_text(encoding='utf-8'))
    errors.write_text(safe_errors,encoding='utf-8')
    assert code == 0, safe_errors
    assert complete and complete['checks'] >= 30
    (output/'browser-results.json').write_text(json.dumps(complete,indent=2),encoding='utf-8')
