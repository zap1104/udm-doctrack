"""Private validated filters; opening always reuses current repository access."""

from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import QueryDict
from django.urls import reverse

from apps.accounts.models import Office, User

from .models import Collection, Document, SavedDocumentView


def _reader(user, *, lock=False):
    users = User.objects.select_for_update(no_key=True) if lock else User.objects
    actor = users.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or actor.role not in {"USER", "VIEWER", "ADMIN", "SYSTEM_ADMIN"}:
        raise PermissionDenied("An active DocTrack account is required.")
    return actor


def validated_filters(*, user, collection, params):
    if collection not in Collection.values:
        raise ValidationError("Choose the OVPA or External repository.")
    if not isinstance(params, QueryDict):
        raise ValidationError("Use repository filter parameters.")
    visible = Document.objects.visible_to(user).in_collection(collection).filter(is_active=True)
    if collection == Collection.EXTERNAL:
        from .external_forms import ExternalRepositoryFilterForm

        form = ExternalRepositoryFilterForm(params, user=user, visible=visible)
    else:
        from .forms import RepositoryFilterForm
        from .views import RepositoryView

        options = RepositoryView._options(visible)
        options["offices"] = Office.objects.filter(pk__in=visible.values("office_id"), is_active=True)
        form = RepositoryFilterForm(params, **options)
    if len(params.urlencode()) > 4000 or set(params) - set(form.fields):
        raise ValidationError("This view contains unsupported filters.")
    if any(len(params.getlist(key)) != 1 for key in params):
        raise ValidationError("Each saved filter must have one value.")
    if not form.is_valid():
        raise ValidationError("These filters are invalid or no longer available to your account.")
    return {key: params[key] for key in params if params[key] != ""}


@transaction.atomic
def create_saved_view(*, user, name, collection, params):
    actor = _reader(user, lock=True)
    name = " ".join(str(name).split())
    if not name or len(name) > 80:
        raise ValidationError("Give this view a name between 1 and 80 characters.")
    filters = validated_filters(user=actor, collection=collection, params=params)
    if SavedDocumentView.objects.filter(owner=actor, name__iexact=name).exists():
        raise ValidationError("You already have a saved view with this name.")
    return SavedDocumentView.objects.create(owner=actor, name=name, collection=collection, filters=filters)


def open_saved_view(view_id, *, user):
    actor = _reader(user)
    view = SavedDocumentView.objects.filter(pk=view_id, owner=actor).first()
    if view is None:
        raise PermissionDenied("This saved view belongs to another account or is unavailable.")
    params = QueryDict("", mutable=True)
    for key, value in view.filters.items():
        if not isinstance(value, str):
            raise ValidationError("This saved view contains invalid filters.")
        params[key] = value
    filters = validated_filters(user=actor, collection=view.collection, params=params)
    target = "documents:external_repository" if view.collection == Collection.EXTERNAL else "documents:repository"
    return reverse(target) + ("?" + urlencode(filters) if filters else "")


@transaction.atomic
def delete_saved_view(view_id, *, user):
    actor = _reader(user, lock=True)
    view = SavedDocumentView.objects.select_for_update().filter(pk=view_id, owner=actor).first()
    if view is None:
        raise PermissionDenied("This saved view belongs to another account or is unavailable.")
    view.delete()
