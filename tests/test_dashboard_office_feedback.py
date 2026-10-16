"""The dashboard explains an invalid office selection before showing all offices."""

import pytest
from django.contrib.messages import WARNING, get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory

from apps.core.views import DashboardView


def dashboard_scope(user, office):
    request = RequestFactory().get("/", {"office": office})
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    view = DashboardView()
    view.setup(request)
    return view._scope(), list(get_messages(request))


@pytest.mark.django_db
@pytest.mark.parametrize("office", ["missing-office", "999999", "9" * 100])
def test_invalid_dashboard_office_explains_the_all_offices_fallback(users, office):
    scope, feedback = dashboard_scope(users["admin"], office)

    assert scope["office"] is None
    assert scope["all_offices"]
    assert len(feedback) == 1
    assert feedback[0].level == WARNING
    assert "office" in str(feedback[0]).lower()


@pytest.mark.django_db
def test_inactive_dashboard_office_explains_the_all_offices_fallback(users, offices):
    offices["MED"].is_active = False
    offices["MED"].save(update_fields=["is_active"])

    scope, feedback = dashboard_scope(users["admin"], str(offices["MED"].pk))

    assert scope["office"] is None
    assert scope["all_offices"]
    assert len(feedback) == 1
    assert feedback[0].level == WARNING


@pytest.mark.django_db
@pytest.mark.parametrize("office", ["", "all", "MED", "med"])
def test_supported_dashboard_office_selection_has_no_warning(users, offices, office):
    scope, feedback = dashboard_scope(users["admin"], office)

    assert scope["office"] == (offices["MED"] if office.lower() == "med" else None)
    assert scope["all_offices"] is (office in {"", "all"})
    assert feedback == []
