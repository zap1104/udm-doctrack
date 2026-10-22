import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.core.models import Tag
from apps.documents.models import Document, TagPrediction, TagReview
from apps.documents.tagging import input_revision, review_tag, vocabulary_digest

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def population(users):
    def create(actor, collection="OVPA", **extra):
        return Document.objects.create(
            title=f"Review {actor.username}",
            year=2026,
            office=actor.office,
            uploaded_by=actor,
            access_level="OFFICE",
            collection=collection,
            **extra,
        )

    own = [create(users["med"]) for _ in range(15)]
    return own, create(users["hr"]), create(users["med_admin"], "EXTERNAL")


def test_reproducible_random_audit_is_scoped_and_get_does_not_label(client, users, population):
    own, foreign, external = population
    client.force_login(users["med"])
    url = reverse("documents:tag_review_queue") + "?seed=defense-1"
    a, b = client.get(url), client.get(url)
    assert a.status_code == b.status_code == 200
    rows = a.context["samples"]
    assert [(r["document"].pk, r["method"]) for r in rows] == [
        (r["document"].pk, r["method"]) for r in b.context["samples"]
    ]
    assert {r["document"].pk for r in rows} <= {d.pk for d in own}
    assert rows and all(r["method"] == "random_audit" for r in rows)
    assert TagReview.objects.count() == TagPrediction.objects.count() == 0
    for bad in ("?collection=BAD", "?collection=OVPA&collection=EXTERNAL", "?office=999999", "?seed=a&seed=b"):
        assert client.get(reverse("documents:tag_review_queue") + bad).status_code == 400
    for actor in (users["viewer"],):
        client.force_login(actor)
        assert client.get(url).status_code == 403
    client.force_login(users["med_admin"])
    page = client.get(reverse("documents:tag_review_queue") + "?collection=EXTERNAL&seed=defense-1")
    assert [r["document"].pk for r in page.context["samples"]] == [external.pk]


def test_uncertainty_samples_use_current_scoped_predictions(users, population):
    from apps.documents.ml.lifecycle import activate_model
    from apps.documents.review_sampling import review_samples
    from tests.ml_fixtures import make_candidate

    own, _, _ = population
    model = make_candidate(users)
    tag = Tag.objects.get(pk=model.topic_fixture_id)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    # Choose documents outside the independent random portion. IDs vary with
    # suite order; already-audited uncertain documents must not be duplicated.
    audit_ids = {row["document"].pk for row in review_samples(user=users["med"], collection="OVPA", seed="mixed")[:10]}
    candidates = [doc for doc in own if doc.pk not in audit_ids][:3]
    assert len(candidates) == 3
    for i, doc in enumerate(candidates):
        TagPrediction.objects.create(
            document=doc,
            tag=tag,
            engine="ml",
            decision="suggest",
            model_reference=str(model.pk),
            score=0.51 + i * 0.1,
            input_revision=input_revision(doc) if i != 2 else "stale",
            vocabulary=vocabulary_digest(),
            evidence={"mode": "suggestion", "threshold": 0.5},
        )
    rows = review_samples(user=users["med"], collection="OVPA", seed="mixed")
    uncertain = [r for r in rows if r["method"] == "uncertainty"]
    assert {row["document"].pk for row in uncertain} == {doc.pk for doc in candidates[:2]}
    distances = [abs(r["score"] - r["threshold"]) for r in uncertain]
    assert distances == sorted(distances)
    assert all(r["document"].pk != candidates[2].pk for r in uncertain)
    assert any(r["method"] == "random_audit" for r in rows)


def test_random_audits_already_covering_uncertainty_are_not_duplicated(users, population):
    from apps.documents.ml.lifecycle import activate_model
    from apps.documents.review_sampling import review_samples
    from tests.ml_fixtures import make_candidate

    model = make_candidate(users)
    activate_model(model.pk, user=users["admin"], mode="suggestion")
    audit = review_samples(user=users["med"], collection="OVPA", seed="covered")
    for row in audit[:2]:
        doc = row["document"]
        TagPrediction.objects.create(
            document=doc,
            tag_id=model.topic_fixture_id,
            engine="ml",
            decision="suggest",
            model_reference=str(model.pk),
            score=0.51,
            input_revision=input_revision(doc),
            vocabulary=vocabulary_digest(),
            evidence={"mode": "suggestion", "threshold": 0.5},
        )
    samples = review_samples(user=users["med"], collection="OVPA", seed="covered")
    assert len(samples) == len({row["document"].pk for row in samples})
    assert all(row["method"] == "random_audit" for row in samples)
    assert {row["document"].pk for row in audit} == {row["document"].pk for row in samples}


def test_signed_sampling_provenance_is_saved_not_trusted_from_post(client, users, population):
    own, _, _ = population
    tag = Tag.objects.create(name="Reviewed topic", globally_approved=True)
    client.force_login(users["med"])
    page = client.get(reverse("documents:tag_review_queue") + "?seed=audit-proof")
    row = page.context["samples"][0]
    doc, token = row["document"], row["token"]
    source = client.get(reverse("documents:source_units", args=[doc.pk]), {"review_sample": token})
    assert source.context["review_form"].initial["review_sample"] == token
    response = client.post(
        reverse("documents:tag_review", args=[doc.pk]),
        {
            "tag": tag.pk,
            "decision": "confirm",
            "input_revision": input_revision(doc),
            "vocabulary": vocabulary_digest(),
            "review_sample": token,
        },
    )
    assert response.status_code == 302
    receipt = doc.tag_reviews.get().sampling
    assert receipt["source"] == "random_audit" and receipt["seed"] == "audit-proof"
    assert receipt["actor_id"] == users["med"].pk
    for bad_doc, actor, bad_token in (
        (doc, users["med"], token + "x"),
        (own[-1] if own[-1].pk != doc.pk else own[0], users["med"], token),
        (doc, users["med_admin"], token),
    ):
        with pytest.raises((PermissionDenied, ValidationError)):
            review_tag(
                bad_doc,
                tag_id=tag.pk,
                decision="reject",
                user=actor,
                expected_revision=input_revision(bad_doc),
                vocabulary=vocabulary_digest(),
                sampling_token=bad_token,
            )
    Document.objects.filter(pk=doc.pk).update(title="Content changed")
    with pytest.raises(ValidationError):
        review_tag(
            doc,
            tag_id=tag.pk,
            decision="reject",
            user=users["med"],
            expected_revision=input_revision(doc),
            vocabulary=vocabulary_digest(),
            sampling_token=token,
        )
    assert doc.tag_reviews.count() == 1


def test_manual_reviews_do_not_invent_sampling_history(users, population):
    doc = population[0][0]
    tag = Tag.objects.create(name="Manual topic", globally_approved=True)
    review_tag(
        doc,
        tag_id=tag.pk,
        decision="unsure",
        user=users["med"],
        expected_revision=input_revision(doc),
        vocabulary=vocabulary_digest(),
    )
    assert doc.tag_reviews.get().sampling == {"source": "manual"}


def test_complete_sample_review_and_expiry_are_enforced(client, users, population, monkeypatch):
    import time

    from django.core import signing

    from apps.documents.review_sampling import SALT

    tag = Tag.objects.create(name="Complete review topic", globally_approved=True)
    client.force_login(users["med"])
    row = client.get(reverse("documents:tag_review_queue"), {"seed": "complete"}).context["samples"][0]
    doc = row["document"]
    endpoint = reverse("documents:tag_review_complete", args=[doc.pk])
    data = {
        "covered": [tag.pk],
        "positive": [tag.pk],
        "input_revision": input_revision(doc),
        "vocabulary": vocabulary_digest(),
        "review_sample": row["token"],
    }
    assert client.post(endpoint, data).status_code == 302
    review = doc.tag_reviews.get()
    assert review.complete_review and review.sampling["source"] == "random_audit"
    receipt = signing.loads(row["token"], salt=SALT)
    with monkeypatch.context() as signing_clock:
        signing_clock.setattr(
            signing.TimestampSigner, "timestamp", lambda self: signing.b62_encode(int(time.time()) - 8 * 86400)
        )
        data["review_sample"] = signing.dumps(receipt, salt=SALT, compress=True)
    # The timestamp signer uses seconds, so eight days is beyond the seven-day lifetime.
    assert client.post(endpoint, data).status_code == 409
    assert doc.tag_reviews.count() == 1


def test_queue_rechecks_stale_accounts_and_own_office(users, population):
    from apps.accounts.models import User
    from apps.documents.review_sampling import review_samples

    with pytest.raises(ValidationError):
        review_samples(user=users["med"], collection="OVPA", seed="valid", office_id=users["hr"].office_id)
    User.objects.filter(pk=users["med"].pk).update(role="VIEWER")
    with pytest.raises(PermissionDenied):
        review_samples(user=users["med"], collection="OVPA", seed="valid")
