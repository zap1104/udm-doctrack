"""Database access boundaries for DocTrack's PostgreSQL deployment."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction

_request_actor = ContextVar("doctrack_request_actor", default=None)


@lru_cache(maxsize=1)
def _protected_tables():
    from .row_policies import predicates

    return ["public." + table for table, *_ in predicates()]


@contextmanager
def request_scope(actor_id):
    """Scope queries on the restricted login to this transaction, including on a pooler."""
    with transaction.atomic(durable=True), connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT current_user = %s AND session_user = current_user
                AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
                AND NOT EXISTS (SELECT FROM pg_auth_members WHERE member = pg_roles.oid)
                AND NOT EXISTS (
                    SELECT FROM unnest(%s::text[]) AS t(name)
                    WHERE NOT pg_catalog.row_security_active(t.name::regclass)
                )
            FROM pg_roles WHERE rolname = current_user
        """,
            [settings.DOCTRACK_WEB_ROLE, _protected_tables()],
        )
        if cursor.fetchone() != (True,):
            raise ImproperlyConfigured(
                "Request row security requires the dedicated restricted web login and installed policies."
            )
        cursor.execute("SELECT set_config('doctrack.actor_id', %s, true)", [str(actor_id)])
        token = _request_actor.set(actor_id)
        try:
            yield
        finally:
            _request_actor.reset(token)


@contextmanager
def _scoped_setting(name, value):
    if _request_actor.get() is None:
        yield
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting(%s, true)", [name])
        previous = cursor.fetchone()[0] or ""
        cursor.execute("SELECT set_config(%s, %s, true)", [name, value])
        try:
            yield
        finally:
            if not connection.needs_rollback:
                cursor.execute("SELECT set_config(%s, %s, true)", [name, previous])


def locked_row(record):
    """Carry custody across the writes of one already locked workflow transition."""
    return _scoped_setting("doctrack.locked_row", f"{record._meta.db_table}:{record.pk}")


def authorized_operation(area, action):
    return _scoped_setting("doctrack.operation", f"{area}:{action}")


def on_commit(callback, *, robust=False):
    """After-commit work gets a fresh scoped transaction for the originating account."""
    actor = _request_actor.get()
    if actor is None:
        return transaction.on_commit(callback, robust=robust)

    def scoped_callback():
        with request_scope(actor):
            callback()

    return transaction.on_commit(scoped_callback, robust=robust)


class RequestRowSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DOCTRACK_WEB_ROLE:
            return self.get_response(request)
        # Authentication and the OTP gate run before binding the actor.
        actor = request.user.pk if request.user.is_authenticated else 0
        with request_scope(actor):
            response = self.get_response(request)
            if response.status_code >= 500:
                transaction.set_rollback(True)
            return response
