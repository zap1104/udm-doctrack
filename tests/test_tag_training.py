from importlib.util import find_spec

import pytest


def authored_dataset():
    rows = []
    splits = {"train": [], "calibration": [], "development": [], "test": []}
    for i in range(100):
        family = f"fixture-{i}"
        partition = "train" if i < 60 else "calibration" if i < 75 else "development" if i < 90 else "test"
        splits[partition].append(family)
        positive = i % 2 == 0
        rows.append(
            {
                "document_id": i,
                "family": family,
                "title": f"Office record {i}",
                "header": "UDM authored test fixture",
                "body": ("purchase procurement supplier" if positive else "maintenance repair facilities")
                + f" distinctrecord{i}"
                + (" holdoutcanaryxyz" if partition == "test" else ""),
                "labels": {"1": int(positive), "2": None if i % 3 else int(positive), "3": 1},
                "office_id": 1,
                "collection": "OVPA",
                "coverage_complete": True,
            }
        )
    return {
        "schema": "tag-dataset-1",
        "synthetic": True,
        "examples": rows,
        "splits": splits,
        "vocabulary": "fixture-vocabulary",
    }


def training_api():
    assert find_spec("apps.documents.ml.training"), "Real CPU training is missing"
    from apps.documents.ml import training

    return training


def test_real_known_label_training_and_safe_reload():
    training = training_api()
    from apps.documents.ml.artifacts import load_bundle, save_bundle

    payload = authored_dataset()
    bundle = training.fit_bundle(payload, scope={"office_id": 1, "collection": "OVPA"})
    assert set(bundle.classifiers) == {"1", "2"}
    assert len(bundle.classifiers["2"]["training_ids"]) == 20
    assert not bundle.classifiers["1"]["calibrated"]
    assert bundle.evaluation["production_eligible"] is False
    assert bundle.evaluation["tags"]["1"]["automatic_qualified"] is False
    assert "holdoutcanaryxyz" not in " ".join(" ".join(branch["vocabulary"]) for branch in bundle.branches.values())
    serialized = save_bundle(bundle)
    restored = load_bundle(serialized, scope={"office_id": 1, "collection": "OVPA"})
    inputs = [
        payload["examples"][0],
        payload["examples"][1],
        {"title": "", "header": "", "body": "签名 Filipino dokumento"},
    ]
    for expected, actual in zip(bundle.predict(inputs), restored.predict(inputs), strict=True):
        assert actual == pytest.approx(expected, abs=1e-10)
    assert bundle.predict(inputs)[0][1] > bundle.predict(inputs)[1][1]
    assert bundle.feature_settings["zone_weights"] == {"title": 2.0, "header": 1.5, "body": 1.0}


def test_single_class_and_partition_leakage_are_not_repaired_with_fake_labels():
    training = training_api()
    from django.core.exceptions import ValidationError

    payload = authored_dataset()
    payload["splits"]["test"].append(payload["splits"]["train"][0])
    with pytest.raises(ValidationError):
        training.fit_bundle(payload, scope={"office_id": 1, "collection": "OVPA"})


def test_sigmoid_calibration_uses_only_separate_known_families():
    training = training_api()
    payload = authored_dataset()
    payload["splits"] = {"train": [], "calibration": [], "development": [], "test": []}
    rows = []
    for i in range(300):
        row = dict(payload["examples"][i % 100])
        row.update(document_id=i, family=f"calibration-fixture-{i}", body=row["body"] + f" independent{i}")
        partition = "train" if i < 180 else "calibration" if i < 225 else "development" if i < 270 else "test"
        payload["splits"][partition].append(row["family"])
        rows.append(row)
    payload["examples"] = rows
    bundle = training.fit_bundle(payload, scope={"office_id": 1, "collection": "OVPA"})
    classifier = bundle.classifiers["1"]
    assert classifier["calibrated"] is True
    assert set(classifier["calibration_ids"]) == set(range(180, 225))
    assert set(classifier["training_ids"]) == set(range(180))
    assert bundle.evaluation["tags"]["1"]["predictions"] < 100
    assert bundle.evaluation["production_eligible"] is False


@pytest.mark.django_db
def test_prepare_train_evaluate_candidate_loop_stays_inactive(users):
    from tests.ml_fixtures import make_candidate

    model = make_candidate(users, n=70)
    assert model.is_active is False
    assert model.mode == "candidate"
    assert model.evaluation["synthetic"] is True
    report = training_api().evaluate_candidate(model.pk, user=users["admin"])
    assert str(model.topic_fixture_id) in report


def test_late_topic_signal_is_in_the_fitted_body_vocabulary():
    payload = authored_dataset()
    payload["examples"] = payload["examples"][:20]
    payload["splits"] = {
        "train": [row["family"] for row in payload["examples"]],
        "calibration": [],
        "development": [],
        "test": [],
    }
    for i, row in enumerate(payload["examples"]):
        row["body"] = "shared early text " * 12000 + (
            " lateprocurementcanary" if i % 2 == 0 else " latefacilitiescanary"
        )
    bundle = training_api().fit_bundle(payload, scope={"office_id": 1, "collection": "OVPA"})
    assert "lateprocurementcanary" in bundle.branches["body_word"]["vocabulary"]
    assert bundle.predict(payload["examples"][:1])[0][1] > bundle.predict(payload["examples"][1:2])[0][1]
