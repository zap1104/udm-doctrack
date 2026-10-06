"""Renaming MED must preserve the office and all existing associations."""

from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection

from apps.accounts.models import Office, User
from apps.tracking.services import create_draft_record


@pytest.mark.django_db
def test_med_rename_changes_only_the_name_and_is_safe_to_repeat(users, offices, memo_type):
    Office.objects.filter(pk=offices["MED"].pk).update(name="Maintenance and Engineering Department")
    record = create_draft_record(user=users["med"], subject="Existing maintenance request",
                                 instructions="For review", document_type=memo_type)
    office_rows = list(Office.objects.order_by("pk").values())
    members = list(User.objects.order_by("pk").values("pk", "office_id", "role"))
    record_before = (record.originating_office_id, record.current_office_id, record.tracking_number)
    expected = [
        {**row, "name": "Maintenance and Engineering Division"} if row["code"] == "MED" else row
        for row in office_rows
    ]
    migration = import_module("apps.accounts.migrations.0008_med_office_division_name")
    for _ in range(2):
        migration.rename_med(apps, SimpleNamespace(connection=connection))
        assert list(Office.objects.order_by("pk").values()) == expected
        assert list(User.objects.order_by("pk").values("pk", "office_id", "role")) == members
        record.refresh_from_db()
        assert (record.originating_office_id, record.current_office_id, record.tracking_number) == record_before
