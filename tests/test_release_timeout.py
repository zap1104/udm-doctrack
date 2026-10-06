"""Malformed idle-warning links must remain safe on anonymous pages."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from apps.core.context_processors import site_context
from apps.core.middleware import idle_seconds_for_request


@pytest.fixture
def idle_windows(settings):
    settings.SESSION_COOKIE_AGE = 1800
    settings.SESSION_COOKIE_AGE_ADMIN = 900


@pytest.mark.parametrize("raw", ["\u00b2", "9" * 5000, "bad", "", "1", "-900"],
                         ids=["superscript", "oversized", "text", "empty", "unconfigured", "negative"])
def test_anonymous_context_ignores_malformed_timeout_values(idle_windows, raw):
    request = RequestFactory().get("/accounts/login/", {"timeout": raw})
    request.user = AnonymousUser()

    assert site_context(request)["SESSION_IDLE_SECONDS"] == 1800


@pytest.mark.parametrize("raw, expected", [("900", 900), ("1800", 1800), (" 900 ", 900), (" 1800 ", 1800)])
def test_anonymous_timeout_accepts_configured_windows(idle_windows, raw, expected):
    request = RequestFactory().get("/accounts/login/", {"timeout": raw})
    request.user = AnonymousUser()

    assert idle_seconds_for_request(request) == expected


@pytest.mark.parametrize("is_admin, expected", [(False, 1800), (True, 900)])
@pytest.mark.parametrize("raw", ["900", "1800", "\u00b2", "9" * 5000],
                         ids=["admin", "ordinary", "superscript", "oversized"])
def test_authenticated_timeout_uses_current_role_window(idle_windows, is_admin, expected, raw):
    request = RequestFactory().get("/", {"timeout": raw})
    request.user = SimpleNamespace(is_authenticated=True, is_office_admin=is_admin)

    assert idle_seconds_for_request(request) == expected
