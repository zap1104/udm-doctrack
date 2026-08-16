"""Shared fixtures. Every test runs against a real PostgreSQL test database."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from apps.accounts.models import Office
from apps.core.models import DocumentType, Tag

User = get_user_model()


class HttpsClient(Client):
    """Test client that always speaks HTTPS.

    CI runs with ``DJANGO_DEBUG=False``, which switches on
    ``SECURE_SSL_REDIRECT``: over plain http every request is answered with a
    301 and never reaches the view, and ``SESSION_COOKIE_SECURE`` would stop the
    session cookie coming back besides. The deployed app is served over HTTPS,
    so driving it this way is also the faithful thing to do.

    This lives in conftest rather than in one test module because that is
    exactly how it went wrong: it was defined in test_login_lockout.py, those
    twenty tests passed, and every later test file quietly got the plain-http
    client instead and only failed on CI.
    """

    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def client():
    """Replaces pytest-django's client with the HTTPS one above."""
    return HttpsClient()


@pytest.fixture
def second_client():
    """An independent browser, for tests about two devices at once."""
    return HttpsClient()


@pytest.fixture
def offices(db):
    return {
        code: Office.objects.create(code=code, name=name, cluster="OVPA")
        for code, name in [
            ("MED", "Mechanical and Engineering Department"),
            ("SUP", "Supply and Property Management"),
            ("HR", "Human Resource Management Office"),
            ("REC", "Records Management Office"),
        ]
    }


@pytest.fixture
def users(db, offices):
    made = {}
    for username, code, role in [
        ("med", "MED", "USER"),
        ("sup", "SUP", "USER"),
        ("hr", "HR", "USER"),
        ("admin", "REC", "ADMIN"),
    ]:
        user = User.objects.create_user(
            username=username, password="TestPass123!", office=offices[code], role=role
        )
        if role == "ADMIN":
            user.is_staff = user.is_superuser = True
            user.save()
        made[username] = user
    return made


@pytest.fixture
def memo_type(db):
    return DocumentType.objects.create(code="MEMO", name="Memorandum", retention_years=5)


@pytest.fixture
def tag_urgent(db):
    tag, _created = Tag.get_or_create_by_name("urgent", category="priority")
    return tag
