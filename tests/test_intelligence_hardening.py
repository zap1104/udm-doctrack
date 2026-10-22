import hashlib
import io
import json
import tracemalloc
import zipfile

import pytest
from django.core.exceptions import ValidationError

from apps.documents.models import Document, DocumentFile, ExtractionRun, ExtractionUnit
from apps.documents.tagging import input_revision
from tests.test_tag_training import authored_dataset, training_api


@pytest.mark.django_db
def test_input_revision_streams_all_units_and_detects_a_late_change(users):
    doc = Document.objects.create(title="Memory fixture", office=users["med"].office, year=2026)
    file = DocumentFile.objects.create(document=doc, file="memory.txt", original_name="memory.txt")
    from apps.documents.processing_contracts import resolve_file_version

    run = ExtractionRun.objects.create(
        document=doc, file_version=resolve_file_version(file), generation=0, settings_digest="a" * 64, is_current=True
    )
    for ordinal in range(24):
        ExtractionUnit.objects.create(
            run=run, ordinal=ordinal, locator_key=f"unit:{ordinal}", normalized_text="x" * 500_000
        )
    tracemalloc.start()
    revision = input_revision(doc)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 10_000_000, f"revision hashing allocated {peak} bytes for a 12MB source"
    ExtractionUnit.objects.filter(run=run, ordinal=23).update(normalized_text="x" * 499_999 + "y")
    assert input_revision(doc) != revision


@pytest.mark.parametrize("mutation", ["branches", "classifier", "calibrated", "parameter", "deep"])
def test_malformed_numeric_bundle_always_returns_validation_error(mutation):
    from apps.documents.ml.artifacts import load_bundle, save_bundle

    bundle = training_api().fit_bundle(authored_dataset(), scope={"office_id": 1, "collection": "OVPA"})
    artifact = save_bundle(bundle)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(artifact["data"])) as source, zipfile.ZipFile(output, "w") as target:
        manifest = json.loads(source.read("manifest.json"))
        if mutation == "branches":
            manifest["branches"] = []
        elif mutation == "classifier":
            manifest["classifiers"]["1"] = []
        elif mutation == "calibrated":
            manifest["classifiers"]["1"]["calibrated"] = "false"
        elif mutation == "parameter":
            manifest["classifiers"]["1"]["threshold"] = "0.5"
        for name in source.namelist():
            data = source.read(name)
            if name == "manifest.json":
                data = ("[" * 1200 + "0" + "]" * 1200).encode() if mutation == "deep" else json.dumps(manifest).encode()
            target.writestr(name, data)
    data = output.getvalue()
    with pytest.raises(ValidationError):
        load_bundle({**artifact, "data": data, "digest": hashlib.sha256(data).hexdigest()}, scope=bundle.scope)


def test_high_diversity_training_is_rejected_before_unbounded_vocabulary_growth():
    from apps.documents.ml.features import SafeTfidf

    vectorizer = SafeTfidf(analyzer="word", ngram_range=(1, 2), max_features=20_000)
    with pytest.raises(ValidationError, match="feature.*budget"):
        vectorizer.fit_transform([" ".join(f"word{i}" for i in range(90_000))])
