"""Shared fixtures. Every test runs against a real PostgreSQL test database."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.models import Office
from apps.core.models import DocumentType, Tag

User = get_user_model()


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
