"""The Administration screens: what each administrator sees, and how they narrow it.

An office administrator administers one office. Every screen here was built for
a global administrator first and scoped afterwards, and the scoping reached the
lists before it reached the summaries around them: the home page's counts and
its "Latest activity" still answered for the whole university.
"""

from __future__ import annotations

import pytest

from apps.core.models import AuditLog

HOME = "/administration/"


@pytest.fixture
def logged(users):
    """One audit entry by an account in each of two offices."""
    AuditLog.objects.create(
        actor=users["med"], actor_label="med", action=AuditLog.Action.UPDATE,
        summary="MED changed its own document",
    )
    AuditLog.objects.create(
        actor=users["sup"], actor_label="sup", action=AuditLog.Action.UPDATE,
        summary="SUP changed its own document",
    )


# --- the home page answers for what the reader administers -------------------
@pytest.mark.django_db
def test_an_office_administrator_sees_only_their_office_in_latest_activity(
    client, users, logged
):
    client.force_login(users["med_admin"])

    summaries = [entry.summary for entry in client.get(HOME).context["recent_audit"]]

    assert "MED changed its own document" in summaries
    assert "SUP changed its own document" not in summaries


@pytest.mark.django_db
def test_a_system_administrator_sees_every_office_in_latest_activity(client, users, logged):
    client.force_login(users["admin"])

    summaries = [entry.summary for entry in client.get(HOME).context["recent_audit"]]

    assert {"MED changed its own document", "SUP changed its own document"} <= set(summaries)


@pytest.mark.django_db
def test_the_user_count_is_the_accounts_the_card_opens(client, users):
    client.force_login(users["med_admin"])
    home = client.get(HOME).context["user_count"]
    listed = client.get("/accounts/users/?per_page=100").context["page_obj"].paginator.count

    assert home == listed


@pytest.mark.django_db
def test_an_office_administrator_is_not_offered_the_offices_screen(client, users):
    """It is a system administrator's screen, and the card and tab that opened
    it answered an office administrator with a 403."""
    client.force_login(users["med_admin"])

    for page in (HOME, "/accounts/users/"):
        assert 'href="/accounts/offices/"' not in client.get(page).content.decode(), page
    assert client.get("/accounts/offices/").status_code == 403


@pytest.mark.django_db
def test_a_system_administrator_is_offered_the_offices_screen(client, users):
    client.force_login(users["admin"])

    for page in (HOME, "/accounts/users/"):
        assert 'href="/accounts/offices/"' in client.get(page).content.decode(), page


# --- accounts: role, status, email -------------------------------------------
USERS = "/accounts/users/"


def _listed(client, query=""):
    response = client.get(f"{USERS}?per_page=100{query}")
    return {row.username for row in response.context["users"]}, response


@pytest.mark.django_db
def test_accounts_filter_by_role(client, users):
    client.force_login(users["admin"])

    listed, _ = _listed(client, "&role=ADMIN")

    assert listed == {"med_admin", "sup_admin"}


@pytest.mark.django_db
def test_accounts_filter_by_status(client, users):
    suspended = users["hr"]
    suspended.is_active = False
    suspended.save(update_fields=["is_active"])
    client.force_login(users["admin"])

    assert _listed(client, "&status=suspended")[0] == {"hr"}
    assert "hr" not in _listed(client, "&status=active")[0]


@pytest.mark.django_db
def test_accounts_search_reaches_email(client, users):
    """The one detail an administrator is usually handed."""
    users["sup"].email = "j.cruz@udm.example"
    users["sup"].save(update_fields=["email"])
    client.force_login(users["admin"])

    assert _listed(client, "&q=j.cruz")[0] == {"sup"}


@pytest.mark.django_db
def test_filters_compose_and_stay_inside_the_office(client, users):
    """An office administrator narrowing by role is still inside their office."""
    client.force_login(users["med_admin"])

    listed, response = _listed(client, "&role=USER&status=active")

    assert listed == {"med"}
    assert response.context["is_filtered"] is True
    assert 'name="office"' not in response.content.decode(), "their office is the only one"


@pytest.mark.django_db
def test_an_unknown_role_is_dropped_and_said(client, users):
    client.force_login(users["admin"])

    listed, response = _listed(client, "&role=OVERLORD")

    assert len(listed) == len(users)
    assert "Ignored a filter that was not recognised" in response.content.decode()


# --- the audit log: dates, office, and each panel keeping the other's filters --
AUDIT = "/administration/audit-log/"


@pytest.fixture
def dated_log(users):
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    for who, days, summary in (("med", 40, "Old MED change"), ("med", 1, "New MED change"),
                               ("sup", 1, "New SUP change")):
        entry = AuditLog.objects.create(
            actor=users[who], actor_label=who, action=AuditLog.Action.UPDATE, summary=summary,
        )
        AuditLog.objects.filter(pk=entry.pk).update(created_at=now - timedelta(days=days))


def _summaries(client, query):
    return [entry.summary for entry in client.get(f"{AUDIT}?{query}").context["entries"]]


@pytest.mark.django_db
def test_the_audit_log_filters_by_date(client, users, dated_log):
    from datetime import timedelta

    from django.utils import timezone

    client.force_login(users["admin"])
    since = (timezone.localdate() - timedelta(days=7)).isoformat()

    summaries = _summaries(client, f"since={since}")

    assert "New MED change" in summaries and "New SUP change" in summaries
    assert "Old MED change" not in summaries


@pytest.mark.django_db
def test_dates_the_wrong_way_round_mean_the_same_range(client, users, dated_log):
    from datetime import timedelta

    from django.utils import timezone

    client.force_login(users["admin"])
    today = timezone.localdate()
    forwards = f"since={(today - timedelta(days=7)).isoformat()}&until={today.isoformat()}"
    backwards = f"since={today.isoformat()}&until={(today - timedelta(days=7)).isoformat()}"

    assert _summaries(client, forwards) == _summaries(client, backwards)


@pytest.mark.django_db
def test_a_system_administrator_filters_the_log_by_office(client, users, offices, dated_log):
    client.force_login(users["admin"])

    summaries = _summaries(client, f"office={offices['SUP'].pk}")

    assert summaries == ["New SUP change"]


@pytest.mark.django_db
def test_an_office_administrator_has_no_office_filter_to_widen_with(client, users, offices, dated_log):
    """Their log is their office already, and naming another changes nothing."""
    client.force_login(users["med_admin"])

    response = client.get(f"{AUDIT}?office={offices['SUP'].pk}")

    assert "SUP change" not in " ".join(e.summary for e in response.context["entries"])
    assert 'name="office"' not in response.content.decode()


@pytest.mark.django_db
def test_a_bad_date_is_dropped_and_said(client, users, dated_log):
    client.force_login(users["admin"])

    response = client.get(f"{AUDIT}?since=yesterday")

    summaries = {entry.summary for entry in response.context["entries"]}
    assert {"Old MED change", "New MED change", "New SUP change"} <= summaries, "no date applied"
    assert "Ignored a filter that was not recognised" in response.content.decode()


@pytest.fixture
def opened_and_printed(users, offices, memo_type):
    from apps.tracking.models import RecordActivity
    from apps.tracking.services import create_draft_record

    record = create_draft_record(
        user=users["med"], subject="Read me", instructions="x", document_type=memo_type,
    )
    for event in (RecordActivity.Event.VIEWED, RecordActivity.Event.PRINTED):
        RecordActivity.objects.create(
            record=record, event=event, actor=users["med"], actor_office=offices["MED"],
            message=event.label,
        )
    return record


@pytest.mark.django_db
def test_record_access_filters_opens_from_prints(client, users, opened_and_printed):
    client.force_login(users["admin"])

    printed = client.get(f"{AUDIT}?event=PRINTED").context["access_entries"]

    assert [entry.event for entry in printed] == ["PRINTED"]


@pytest.mark.django_db
def test_filtering_one_trail_keeps_the_other_trails_filters(client, users, dated_log, opened_and_printed):
    """The system log's form carried nothing of the access panel's, so narrowing
    the log reset whatever the administrator had set on record access."""
    client.force_login(users["admin"])

    body = client.get(f"{AUDIT}?event=PRINTED&who=med").content.decode()
    system_form = body[body.index('id="audit-q"') - 2000:body.index('id="audit-q"')]

    assert 'name="event" value="PRINTED"' in system_form
    assert 'name="who" value="med"' in system_form


# --- master data: retired rows, and every column searched ---------------------
@pytest.mark.django_db
def test_master_data_filters_retired_rows(client, users):
    from apps.core.models import Tag

    Tag.objects.create(name="current", slug="current", is_active=True)
    Tag.objects.create(name="retired", slug="retired", is_active=False)
    client.force_login(users["admin"])

    active = {tag.name for tag in client.get("/administration/tags/?status=active").context["objects"]}
    retired = {tag.name for tag in client.get("/administration/tags/?status=inactive").context["objects"]}

    assert "current" in active and "retired" not in active
    assert retired == {"retired"}


@pytest.mark.django_db
def test_master_data_search_reaches_every_text_column(client, users):
    """A document type's code is a column on screen; only the name was searched."""
    from apps.core.models import DocumentType

    DocumentType.objects.create(code="WRKORD", name="Work order", retention_years=5)
    client.force_login(users["admin"])

    found = client.get("/administration/document-types/?q=WRKORD").context["objects"]

    assert [row.name for row in found] == ["Work order"]
