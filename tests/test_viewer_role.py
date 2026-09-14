"""VIEWER: read the record, print the slip, touch nothing.

Enforced twice on purpose. The view layer refuses so the user gets a page rather
than a stack trace; the service layer refuses because hiding a button is not a
permission — the endpoints stay reachable to anyone who knows the URL, and the
service is what every path goes through, views included.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied

from apps.tracking.models import RecordActivity, Status
from apps.tracking.services import (
    add_remark,
    complete_record,
    confirm_receipt,
    create_draft_record,
    grant_access,
    mark_in_process,
    reopen_record,
    route_record,
)


@pytest.fixture
def med_record(users, offices, memo_type):
    """A record MED can see, sent on to SUP and confirmed."""
    record = create_draft_record(
        user=users["med"], subject="Something to look at", instructions="For action.",
        document_type=memo_type,
    )
    route_record(record, [offices["SUP"]], user=users["med"])
    confirm_receipt(record, user=users["sup"])
    record.refresh_from_db()
    return record


# --- the properties --------------------------------------------------------
@pytest.mark.django_db
def test_the_role_properties_say_what_they_mean(users):
    viewer, ordinary = users["viewer"], users["med"]
    office_admin, system_admin = users["med_admin"], users["admin"]

    assert viewer.is_viewer is True
    assert ordinary.is_viewer is False

    # is_system_admin is now the SYSTEM_ADMIN role alone.
    assert system_admin.is_system_admin is True
    assert office_admin.is_system_admin is False
    assert office_admin.is_office_admin is True
    assert ordinary.is_office_admin is False
    # A system administrator holds everything an office administrator holds.
    assert system_admin.is_office_admin is True


# --- reading is allowed ----------------------------------------------------
@pytest.mark.django_db
def test_a_viewer_can_open_a_record_from_their_own_office(client, med_record, users):
    client.force_login(users["viewer"])
    response = client.get(med_record.get_absolute_url())

    assert response.status_code == 200
    assert med_record.tracking_number in response.content.decode()


@pytest.mark.django_db
def test_a_viewer_can_print_the_routing_slip(client, med_record, users):
    client.force_login(users["viewer"])
    response = client.get(f"/tracking/{med_record.pk}/slip/")

    assert response.status_code == 200


@pytest.mark.django_db
def test_a_viewer_still_cannot_read_another_offices_record(client, users, offices, memo_type):
    """View-only does not mean view-everything."""
    other = create_draft_record(
        user=users["hr"], subject="HR business", instructions="For action.", document_type=memo_type,
    )
    route_record(other, [offices["SUP"]], user=users["hr"])

    client.force_login(users["viewer"])
    assert client.get(other.get_absolute_url()).status_code == 403


# --- writing is refused, at both layers ------------------------------------
@pytest.mark.django_db
def test_the_service_layer_refuses_every_mutation(med_record, users, offices, memo_type):
    viewer = users["viewer"]

    with pytest.raises(PermissionDenied):
        create_draft_record(user=viewer, subject="Nope", instructions="Nope.")
    with pytest.raises(PermissionDenied):
        route_record(med_record, [offices["HR"]], user=viewer, action="FORWARD")
    with pytest.raises(PermissionDenied):
        confirm_receipt(med_record, user=viewer)
    with pytest.raises(PermissionDenied):
        add_remark(med_record, user=viewer, remark="Nope.")
    with pytest.raises(PermissionDenied):
        mark_in_process(med_record, user=viewer)
    with pytest.raises(PermissionDenied):
        complete_record(med_record, user=viewer)
    with pytest.raises(PermissionDenied):
        reopen_record(med_record, user=viewer, reason="Nope.")
    with pytest.raises(PermissionDenied):
        grant_access(med_record, user=viewer, office=offices["HR"], reason="Nope.")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path, payload",
    [
        ("remark/", {"remark": "Sneaking one in"}),
        ("receipt/", {}),
        ("complete/", {}),
        ("archive/", {}),
        ("reopen/", {"reason": "because"}),
        ("share/", {}),
    ],
)
def test_every_mutating_endpoint_refuses_a_viewer(client, med_record, users, path, payload):
    client.force_login(users["viewer"])
    response = client.post(f"/tracking/{med_record.pk}/{path}", payload)

    assert response.status_code == 403, path


@pytest.mark.django_db
def test_a_viewer_cannot_reach_the_create_page(client, users):
    client.force_login(users["viewer"])
    assert client.get("/tracking/new/").status_code == 403


@pytest.mark.django_db
def test_the_action_panel_is_not_offered_to_a_viewer(client, med_record, users):
    """A 403 the user could have been spared is still a bug in the page."""
    med_record.refresh_from_db()
    assert med_record.can_user_act(users["viewer"]) is False
    assert med_record.can_user_confirm_receipt(users["viewer"]) is False


@pytest.mark.django_db
def test_a_viewer_cannot_open_the_administration_area(client, users):
    client.force_login(users["viewer"])
    assert client.get("/accounts/users/").status_code == 403


# --- 2.5: what a viewer does is still recorded -----------------------------
@pytest.mark.django_db
def test_opening_a_record_is_logged(client, med_record, users):
    client.force_login(users["viewer"])
    client.get(med_record.get_absolute_url())

    entry = med_record.activities.filter(event=RecordActivity.Event.VIEWED).first()
    assert entry is not None
    assert entry.actor_id == users["viewer"].pk


@pytest.mark.django_db
def test_printing_the_slip_is_logged(client, med_record, users):
    client.force_login(users["viewer"])
    client.get(f"/tracking/{med_record.pk}/slip/")

    entry = med_record.activities.filter(event=RecordActivity.Event.PRINTED).first()
    assert entry is not None
    assert entry.actor_id == users["viewer"].pk


@pytest.mark.django_db
def test_repeated_opens_collapse_to_one_entry(client, med_record, users):
    """One row per page load would bury the movement history under footprints —
    and the timeline is on the page being opened, so each read would lengthen
    the thing being read."""
    client.force_login(users["viewer"])
    for _ in range(4):
        client.get(med_record.get_absolute_url())

    assert med_record.activities.filter(event=RecordActivity.Event.VIEWED).count() == 1


@pytest.mark.django_db
def test_each_print_is_its_own_entry(client, med_record, users):
    """Every print puts another copy outside the system."""
    client.force_login(users["viewer"])
    for _ in range(3):
        client.get(f"/tracking/{med_record.pk}/slip/")

    assert med_record.activities.filter(event=RecordActivity.Event.PRINTED).count() == 3


@pytest.mark.django_db
def test_two_people_opening_it_are_logged_separately(client, second_client, med_record, users):
    client.force_login(users["viewer"])
    client.get(med_record.get_absolute_url())
    second_client.force_login(users["med"])
    second_client.get(med_record.get_absolute_url())

    actors = set(
        med_record.activities.filter(event=RecordActivity.Event.VIEWED)
        .values_list("actor_id", flat=True)
    )
    assert actors == {users["viewer"].pk, users["med"].pk}


@pytest.mark.django_db
def test_a_view_does_not_promote_the_status(client, med_record, users):
    """VIEWED must not read as activity after receipt."""
    assert med_record.status == Status.RECEIVED
    client.force_login(users["viewer"])
    client.get(med_record.get_absolute_url())

    med_record.refresh_from_db()
    assert med_record.status == Status.RECEIVED


@pytest.mark.django_db
def test_views_are_kept_out_of_the_rendered_timeline(client, med_record, users):
    client.force_login(users["viewer"])
    client.get(med_record.get_absolute_url())
    body = client.get(med_record.get_absolute_url()).content.decode()

    assert med_record.activities.filter(event=RecordActivity.Event.VIEWED).exists()
    assert "opened the document" not in body


# ------------------------------------------- buttons that would refuse them
TRACKING_LIST = "/tracking/"
REPOSITORY = "/documents/"


@pytest.mark.django_db
def test_can_start_work_is_false_for_a_viewer(users):
    """The gate behind the create and upload buttons, at the model rather than
    in a view: three pages ask it, and two of them are in other apps."""
    assert users["viewer"].can_start_work is False


@pytest.mark.django_db
def test_can_start_work_is_false_without_an_office(db, offices):
    """`OfficeAssignedMixin` sends these accounts back with a warning, which is
    a poor answer to a button."""
    from django.contrib.auth import get_user_model

    orphan = get_user_model().objects.create_user(
        username="no-office", password="TestPass123!", office=None, role="USER",
    )

    assert orphan.can_start_work is False


@pytest.mark.django_db
def test_can_start_work_is_true_for_an_ordinary_office_user(users):
    assert users["med"].can_start_work is True


@pytest.mark.django_db
def test_a_superuser_may_start_work_without_an_office(db):
    """The one account that is not office-scoped. Kept explicit because the
    office test above would otherwise read as "no office means no"."""
    from django.contrib.auth import get_user_model

    root = get_user_model().objects.create_superuser(
        username="root", password="TestPass123!", office=None,
    )

    assert root.can_start_work is True


@pytest.mark.django_db
def test_a_viewer_is_not_offered_the_new_tracking_slip_button(client, users):
    """RecordCreateView turns a viewer away with a redirect and a warning, so
    the list page stops offering the button that leads there."""
    client.force_login(users["viewer"])
    body = client.get(TRACKING_LIST).content.decode()

    assert "New Tracking Slip" not in body


@pytest.mark.django_db
def test_an_ordinary_user_still_gets_the_new_tracking_slip_button(client, users):
    client.force_login(users["med"])
    body = client.get(TRACKING_LIST).content.decode()

    assert "+ New Tracking Slip" in body


@pytest.mark.django_db
def test_a_viewer_is_not_offered_the_upload_button(client, users):
    """Both places the repository offers it — the page head and the empty
    state — since a viewer with no matching documents sees the second one."""
    client.force_login(users["viewer"])
    body = client.get(REPOSITORY).content.decode()

    assert "Upload Document" not in body


@pytest.mark.django_db
def test_an_ordinary_user_still_gets_the_upload_button(client, users):
    client.force_login(users["med"])
    body = client.get(REPOSITORY).content.decode()

    assert "Upload Document" in body


@pytest.mark.django_db
def test_hiding_those_buttons_is_not_the_permission(client, users):
    """The endpoints stay reachable to anyone who knows the URL, so the views
    have to refuse on their own. The hidden button is a courtesy."""
    client.force_login(users["viewer"])

    assert client.get("/tracking/new/").status_code in (302, 403)
    assert client.get("/documents/upload/").status_code in (302, 403)
