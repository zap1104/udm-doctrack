from importlib.util import find_spec

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from tests.ml_fixtures import make_candidate

pytestmark = pytest.mark.django_db


def api():
    assert find_spec("apps.documents.ml.lifecycle"), "Explicit model lifecycle is missing"
    from apps.documents.ml import lifecycle

    return lifecycle


def test_activation_is_explicit_scoped_and_reversible(users):
    lifecycle = api()
    model = make_candidate(users)
    assert not model.is_active
    with pytest.raises(PermissionDenied):
        lifecycle.activate_model(model.pk, user=users["med_admin"], mode="suggestion")
    active = lifecycle.activate_model(model.pk, user=users["admin"], mode="shadow")
    assert active.mode == "shadow" and active.is_active
    lifecycle.activate_model(model.pk, user=users["admin"], mode="suggestion")
    lifecycle.rollback_model(
        user=users["admin"], office_id=model.office_id, collection=model.collection, target_model_id=None
    )
    model.refresh_from_db()
    assert not model.is_active
    source = model.snapshot.sources.first().document
    source.training_eligible = False
    source.save(update_fields=["training_eligible"])
    with pytest.raises(ValidationError):
        lifecycle.activate_model(model.pk, user=users["admin"], mode="suggestion")
