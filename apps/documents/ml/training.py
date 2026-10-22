"""Local supervised training with known-label masks and frozen holdout evaluation."""

import time

from django.core.exceptions import ValidationError
from django.db import transaction

from .dataset import _sysadmin, known_labels, validate_snapshot


def _representatives(rows):
    result = {}
    for row in sorted(rows, key=lambda item: item["document_id"]):
        result.setdefault(row["family"], row)
    return list(result.values())


def _partition_rows(payload):
    splits = payload["splits"]
    if set(splits) != {"train", "calibration", "development", "test"}:
        raise ValidationError("The dataset must have four fixed partitions.")
    all_keys = [key for keys in splits.values() for key in keys]
    families = {row["family"] for row in payload["examples"]}
    if len(all_keys) != len(set(all_keys)) or set(all_keys) != families:
        raise ValidationError("Training families overlap or are missing from the partitions.")
    if not payload["examples"] or len(payload["examples"]) > 2000:
        raise ValidationError("The dataset is empty or exceeds its row budget.")
    for row in payload["examples"]:
        if sum(len(row.get(key, "")) for key in ("title", "header", "body")) > 1_000_000:
            raise ValidationError("A training input exceeds its budget.")
    return {name: [row for row in payload["examples"] if row["family"] in keys] for name, keys in splits.items()}


def fit_bundle(payload, *, scope, feature_settings=None):
    import copy

    from sklearn.linear_model import LogisticRegression
    from threadpoolctl import threadpool_limits

    from .artifacts import ModelBundle
    from .features import DEFAULT_SETTINGS, build_features, export_branches
    from .metrics import score_metrics

    started = time.perf_counter()
    cpu_started = time.process_time()
    partitions = _partition_rows(payload)
    if any(
        row.get("office_id") != scope["office_id"] or row.get("collection") != scope["collection"]
        for row in payload["examples"]
    ):
        raise ValidationError("A dataset example is outside the model scope.")
    train = partitions["train"]
    if not train:
        raise ValidationError("No independent training families are available.")
    settings = copy.deepcopy(feature_settings or DEFAULT_SETTINGS)
    features = build_features(settings)
    with threadpool_limits(limits=1):
        matrix = features.fit_transform(train)
    if matrix.shape[1] == 0:
        raise ValidationError("No usable text features were found.")
    bundle = ModelBundle(
        scope=scope,
        feature_settings=settings,
        branches=export_branches(features),
        classifiers={},
        vocabulary=payload["vocabulary"],
        _features=features,
    )
    tag_ids = sorted({int(tag) for row in train for tag, value in row["labels"].items() if value is not None})
    if len(tag_ids) > 32:
        raise ValidationError("This candidate exceeds the 32-topic training budget.")
    unavailable = {}
    for tag in tag_ids:
        labels = [known_labels(row, [tag])[tag] for row in train]
        independent = [known_labels(row, [tag])[tag] for row in _representatives(train)]
        if independent.count(1) < 2 or independent.count(0) < 2:
            unavailable[str(tag)] = "At least two independent positives and negatives are required."
            continue
        indices = [i for i, label in enumerate(labels) if label is not None]
        estimator = LogisticRegression(C=1.0, max_iter=300, solver="liblinear", random_state=42)
        with threadpool_limits(limits=1):
            estimator.fit(matrix[indices], [labels[i] for i in indices])
        classifier = {
            "coef": estimator.coef_[0].tolist(),
            "intercept": float(estimator.intercept_[0]),
            "calibrated": False,
            "calibration_coef": 1.0,
            "calibration_intercept": 0.0,
            "threshold": 0.5,
            "training_ids": [train[i]["document_id"] for i in indices],
            "calibration_ids": [],
        }
        calibration = [
            row for row in _representatives(partitions["calibration"]) if known_labels(row, [tag])[tag] is not None
        ]
        y_cal = [known_labels(row, [tag])[tag] for row in calibration]
        if y_cal.count(1) >= 20 and y_cal.count(0) >= 20:
            raw = estimator.decision_function(features.transform(calibration)).reshape(-1, 1)
            calibrator = LogisticRegression(C=1.0, max_iter=300, solver="lbfgs", random_state=42)
            with threadpool_limits(limits=1):
                calibrator.fit(raw, y_cal)
            classifier.update(
                calibrated=True,
                calibration_coef=float(calibrator.coef_[0, 0]),
                calibration_intercept=float(calibrator.intercept_[0]),
                calibration_ids=[r["document_id"] for r in calibration],
            )
        bundle.classifiers[str(tag)] = classifier
    if not bundle.classifiers:
        raise ValidationError("No topic has sufficient independent known labels. Continue manual review.")
    development = _representatives(partitions["development"])
    scores = bundle.predict(development) if development else []
    for tag, classifier in bundle.classifiers.items():
        tag_id = int(tag)
        y_dev = [known_labels(row, [tag_id])[tag_id] for row in development]
        probabilities = [row[tag_id] for row in scores]
        options = []
        for threshold in sorted({0.5} | set(probabilities)):
            metrics = score_metrics(y_dev, probabilities, threshold)
            options.append(
                (
                    metrics["precision"] is not None and metrics["precision"] >= 0.95,
                    metrics["f1"],
                    metrics["precision"] or 0,
                    threshold,
                )
            )
        classifier["threshold"] = max(options)[-1] if options else 0.5
        classifier["development_ids"] = [r["document_id"] for r in development]
    reports = {}
    for name in ("calibration", "development", "test"):
        rows = _representatives(partitions[name])
        predictions = bundle.predict(rows) if rows else []
        report = {}
        for tag, classifier in bundle.classifiers.items():
            tag_id = int(tag)
            labels = [known_labels(row, [tag_id])[tag_id] for row in rows]
            probabilities = [row[tag_id] for row in predictions]
            metrics = score_metrics(labels, probabilities, classifier["threshold"])
            slices = {}
            for dimension, getter in {
                "office": lambda r: str(r["office_id"]),
                "language": lambda r: r.get("language", "unknown"),
                "length": lambda r: (
                    "short" if len(r["body"]) < 2000 else "medium" if len(r["body"]) < 20000 else "long"
                ),
            }.items():
                slices[dimension] = {}
                for key in sorted({getter(row) for row in rows}):
                    mask = [i for i, row in enumerate(rows) if getter(row) == key]
                    slices[dimension][key] = score_metrics(
                        [labels[i] for i in mask], [probabilities[i] for i in mask], classifier["threshold"]
                    )
            metrics["slices"] = slices
            report[tag] = metrics
        reports[name] = report
    qualifying = {}
    real_data = payload.get("synthetic") is False
    for tag, classifier in bundle.classifiers.items():
        heldout = reports["test"][tag]
        dev = reports["development"][tag]
        qualified = bool(
            real_data
            and classifier["calibrated"]
            and heldout["predictions"] >= 100
            and (heldout["precision"] or 0) >= 0.95
            and (heldout["wilson_lower"] or 0) >= 0.95
            and heldout["coverage"] >= 0.01
            and (dev["precision"] or 0) >= 0.95
            and dev["positives"] >= 2
            and dev["known"] - dev["positives"] >= 2
        )
        qualifying[tag] = {
            **heldout,
            "threshold": classifier["threshold"],
            "calibrated": classifier["calibrated"],
            "automatic_qualified": qualified,
        }
    bundle.evaluation = {
        "schema": "tag-evaluation-1",
        "synthetic": payload.get("synthetic", True),
        "production_eligible": any(row["automatic_qualified"] for row in qualifying.values()),
        "tags": qualifying,
        "partitions": reports,
        "unavailable": unavailable,
        "fit_document_ids": [r["document_id"] for r in train],
        "feature_count": matrix.shape[1],
        "matrix_bytes": int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes),
        "wall_seconds": time.perf_counter() - started,
        "cpu_seconds": time.process_time() - cpu_started,
        "runtime": bundle.runtime,
        "holdout_frozen": True,
        "independence": "one preselected lowest-ID representative per family",
        "notes": [
            "Synthetic fixtures cannot qualify production automation.",
            "Scores from uncalibrated classifiers are suggestions only.",
        ],
    }
    return bundle


def train_candidate(snapshot_id, *, user, feature_settings=None):
    from ..models import Document
    from .artifacts import load_bundle, save_bundle
    from .models import TagModel, TrainingSnapshot

    with transaction.atomic():
        _sysadmin(user)
        snapshot = TrainingSnapshot.objects.get(pk=snapshot_id)
        validate_snapshot(snapshot)
        payload = snapshot.payload
    bundle = fit_bundle(
        payload,
        scope={"office_id": snapshot.office_id, "collection": snapshot.collection},
        feature_settings=feature_settings,
    )
    artifact = save_bundle(bundle)
    restored = load_bundle(artifact, scope=bundle.scope)
    parity_inputs = payload["examples"][:10]
    for first, second in zip(bundle.predict(parity_inputs), restored.predict(parity_inputs), strict=True):
        if any(abs(first[tag] - second[tag]) > 1e-10 for tag in first):
            raise ValidationError("Artifact inference parity check failed.")
    with transaction.atomic():
        actor = _sysadmin(user)
        list(Document.objects.select_for_update().filter(pk__in=snapshot.sources.values("document_id")).order_by("pk"))
        snapshot = TrainingSnapshot.objects.select_for_update().get(pk=snapshot_id)
        validate_snapshot(snapshot)
        return TagModel.objects.create(
            snapshot=snapshot,
            office=snapshot.office,
            collection=snapshot.collection,
            created_by=actor,
            artifact=artifact["data"],
            artifact_digest=artifact["digest"],
            artifact_origin=artifact["origin"],
            vocabulary=bundle.vocabulary,
            evaluation=bundle.evaluation,
            runtime=bundle.runtime,
        )


def evaluate_candidate(model_id, *, user, partition="test"):
    from .models import TagModel

    with transaction.atomic():
        _sysadmin(user)
        model = TagModel.objects.get(pk=model_id)
        validate_snapshot(model.snapshot)
        if partition not in {"calibration", "development", "test"}:
            raise ValidationError("Choose a frozen evaluation partition.")
        return model.evaluation["partitions"][partition]
