from importlib.util import find_spec

import pytest
from django.core.exceptions import ValidationError

from tests.test_tag_training import authored_dataset, training_api


def test_artifact_digest_scope_runtime_and_numeric_limits():
    assert find_spec("apps.documents.ml.artifacts"), "Safe model artifacts are missing"
    from apps.documents.ml.artifacts import load_bundle, save_bundle

    bundle = training_api().fit_bundle(authored_dataset(), scope={"office_id": 1, "collection": "OVPA"})
    artifact = save_bundle(bundle)
    with pytest.raises(ValidationError):
        load_bundle(artifact, scope={"office_id": 2, "collection": "OVPA"})
    with pytest.raises(ValidationError):
        load_bundle({**artifact, "data": b"hostile pickle"}, scope=bundle.scope)
    with pytest.raises(ValidationError):
        load_bundle(artifact, scope=bundle.scope, runtime={"python": "0.0"})
    bundle.classifiers["1"]["coef"][0] = float("nan")
    with pytest.raises(ValidationError):
        save_bundle(bundle)


def test_compressed_members_and_oversized_array_headers_are_rejected():
    import hashlib
    import io
    import zipfile

    import numpy as np

    from apps.documents.ml.artifacts import load_bundle, save_bundle

    bundle = training_api().fit_bundle(authored_dataset(), scope={"office_id": 1, "collection": "OVPA"})
    artifact = save_bundle(bundle)
    for hostile_shape in (False, True):
        output = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(artifact["data"])) as original,
            zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_STORED if hostile_shape else zipfile.ZIP_DEFLATED
            ) as changed,
        ):
            for name in original.namelist():
                data = original.read(name)
                if hostile_shape and name == "b0.npy":
                    header = io.BytesIO()
                    np.lib.format.write_array_header_1_0(
                        header, {"descr": "<f8", "fortran_order": False, "shape": (2**60,)}
                    )
                    data = header.getvalue()
                changed.writestr(name, data)
        data = output.getvalue()
        with pytest.raises(ValidationError):
            load_bundle({**artifact, "data": data, "digest": hashlib.sha256(data).hexdigest()}, scope=bundle.scope)
