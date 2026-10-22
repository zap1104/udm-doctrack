"""Bounded JSON + numeric NPY bundles from the private local registry only."""

import hashlib
import io
import json
import platform
import zipfile
from dataclasses import dataclass, field
from importlib.metadata import version

from django.core.exceptions import ValidationError

SCHEMA = "topic-bundle-1"
ORIGIN = "local-training-1"
MAX_BYTES = 128_000_000
MAX_MANIFEST_BYTES = 20_000_000
MAX_ARRAY_ELEMENTS = 10_000_000


def runtime_versions():
    return {
        "python": ".".join(platform.python_version_tuple()[:2]),
        **{key: version(key) for key in ("numpy", "scipy", "scikit-learn")},
    }


@dataclass
class ModelBundle:
    scope: dict
    feature_settings: dict
    branches: dict
    classifiers: dict
    vocabulary: str
    evaluation: dict = field(default_factory=dict)
    runtime: dict = field(default_factory=runtime_versions)
    _features: object = field(default=None, repr=False)

    def predict(self, inputs):
        import numpy as np
        from scipy.special import expit

        from .features import build_features

        if len(inputs) > 2000 or any(
            sum(len(str(row.get(zone, ""))) for zone in ("title", "header", "body")) > 1_000_000 for row in inputs
        ):
            raise ValidationError("Prediction input budget exceeded. No prefix was substituted.")
        if self._features is None:
            self._features = build_features(self.feature_settings, branches=self.branches)
        matrix = self._features.transform(inputs)
        results = [{} for _ in inputs]
        for tag, classifier in self.classifiers.items():
            raw = np.asarray(matrix @ np.asarray(classifier["coef"], dtype=float)).reshape(-1) + classifier["intercept"]
            if classifier["calibrated"]:
                raw = raw * classifier["calibration_coef"] + classifier["calibration_intercept"]
            for row, probability in zip(results, expit(raw), strict=True):
                row[int(tag)] = float(probability)
        return results


def _checked_array(value, *, expected=None):
    import numpy as np

    array = np.asarray(value)
    if (
        array.dtype.kind not in "fiu"
        or array.ndim != 1
        or array.size > MAX_ARRAY_ELEMENTS
        or not np.isfinite(array).all()
    ):
        raise ValidationError("Invalid numeric model array.")
    if expected is not None and array.size != expected:
        raise ValidationError("Model array dimensions do not match.")
    return array.astype(np.float64)


def _validate(bundle):
    import math

    from .features import DEFAULT_SETTINGS, build_features

    build_features(bundle.feature_settings)  # validate recipe without fitting
    expected_names = {f"{zone}_{kind}" for zone in DEFAULT_SETTINGS["zone_weights"] for kind in ("word", "char")}
    if set(bundle.branches) != expected_names or len(bundle.classifiers) > 32:
        raise ValidationError("Unsupported model dimensions.")
    width = 0
    for name, branch in bundle.branches.items():
        vocabulary = branch["vocabulary"]
        cap = bundle.feature_settings["word_features" if name.endswith("word") else "char_features"]
        if (
            not isinstance(vocabulary, dict)
            or len(vocabulary) > cap
            or sorted(vocabulary.values()) != list(range(len(vocabulary)))
        ):
            raise ValidationError("Invalid model vocabulary.")
        if any(not isinstance(key, str) or len(key) > 512 for key in vocabulary):
            raise ValidationError("Invalid vocabulary term.")
        idf = _checked_array(branch["idf"], expected=len(vocabulary))
        if (idf <= 0).any():
            raise ValidationError("Invalid inverse document frequency.")
        width += len(vocabulary)
    for tag, classifier in bundle.classifiers.items():
        if not str(tag).isdigit() or int(tag) <= 0:
            raise ValidationError("Invalid canonical tag ID.")
        _checked_array(classifier["coef"], expected=width)
        for key in ("intercept", "threshold", "calibration_coef", "calibration_intercept"):
            if not math.isfinite(classifier[key]):
                raise ValidationError("Nonfinite classifier parameter.")
        if not 0 <= classifier["threshold"] <= 1:
            raise ValidationError("Invalid threshold.")


def save_bundle(bundle, *, storage=None):
    import numpy as np

    _validate(bundle)
    metadata = {
        "schema": SCHEMA,
        "runtime": bundle.runtime,
        "scope": bundle.scope,
        "feature_settings": bundle.feature_settings,
        "vocabulary": bundle.vocabulary,
        "evaluation": bundle.evaluation,
        "branches": {},
        "classifiers": {},
    }
    arrays = {}
    for i, (name, branch) in enumerate(bundle.branches.items()):
        key = f"b{i}"
        metadata["branches"][name] = {"vocabulary": branch["vocabulary"], "array": key}
        arrays[key] = _checked_array(branch["idf"])
    for i, (tag, classifier) in enumerate(bundle.classifiers.items()):
        key = f"c{i}"
        metadata["classifiers"][tag] = {k: v for k, v in classifier.items() if k != "coef"} | {"array": key}
        arrays[key] = _checked_array(classifier["coef"])
    manifest = json.dumps(metadata, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    if len(manifest) > MAX_MANIFEST_BYTES:
        raise ValidationError("Model vocabulary manifest exceeds its budget.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", manifest)
        for key, array in arrays.items():
            buffer = io.BytesIO()
            np.save(buffer, array, allow_pickle=False)
            archive.writestr(key + ".npy", buffer.getvalue())
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise ValidationError("Model artifact exceeds its storage budget.")
    return {"data": data, "digest": hashlib.sha256(data).hexdigest(), "origin": ORIGIN}


def load_bundle(model, *, scope, runtime=None):
    import numpy as np

    data = model["data"] if isinstance(model, dict) else bytes(model.artifact)
    expected_digest = model["digest"] if isinstance(model, dict) else model.artifact_digest
    origin = model["origin"] if isinstance(model, dict) else model.artifact_origin
    if origin != ORIGIN or len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected_digest:
        raise ValidationError("Untrusted or damaged model artifact.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if (
                len(infos) != len(names)
                or len(infos) > 40
                or "manifest.json" not in names
                or sum(i.file_size for i in infos) > MAX_BYTES
            ):
                raise ValidationError("Invalid model archive.")
            if any(i.compress_type != zipfile.ZIP_STORED or i.flag_bits & 1 for i in infos):
                raise ValidationError("Compressed or encrypted model members are not accepted.")
            if archive.getinfo("manifest.json").file_size > MAX_MANIFEST_BYTES:
                raise ValidationError("Model manifest is too large.")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest["schema"] != SCHEMA
                or manifest["runtime"] != (runtime or runtime_versions())
                or manifest["scope"] != scope
            ):
                raise ValidationError("Model runtime, schema or scope does not match.")
            expected_names = {"manifest.json"}
            for container in (manifest["branches"], manifest["classifiers"]):
                for entry in container.values():
                    key = entry.pop("array")
                    if not isinstance(key, str) or not key[1:].isdigit() or key[0] not in "bc":
                        raise ValidationError("Invalid model member reference.")
                    member = key + ".npy"
                    expected_names.add(member)
                    # Validate the NPY header before NumPy can allocate from a
                    # malicious declared shape, even inside a tiny member.
                    raw = archive.read(member)
                    stream = io.BytesIO(raw)
                    fmt = np.lib.format.read_magic(stream)
                    if fmt != (1, 0):
                        raise ValidationError("Unsupported numeric model format.")
                    shape, _, dtype = np.lib.format.read_array_header_1_0(stream, max_header_size=10_000)
                    if (
                        len(shape) != 1
                        or shape[0] > MAX_ARRAY_ELEMENTS
                        or dtype.kind not in "fiu"
                        or len(raw) != stream.tell() + shape[0] * dtype.itemsize
                    ):
                        raise ValidationError("Unsafe model array header.")
                    array = _checked_array(np.load(io.BytesIO(raw), allow_pickle=False, max_header_size=10_000))
                    entry["idf" if container is manifest["branches"] else "coef"] = array.tolist()
            if names != expected_names:
                raise ValidationError("Unexpected model archive member.")
    except ValidationError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, zipfile.BadZipFile, EOFError) as exc:
        raise ValidationError("The model artifact cannot be loaded safely.") from exc
    bundle = ModelBundle(
        scope=manifest["scope"],
        feature_settings=manifest["feature_settings"],
        branches=manifest["branches"],
        classifiers=manifest["classifiers"],
        vocabulary=manifest["vocabulary"],
        evaluation=manifest["evaluation"],
        runtime=manifest["runtime"],
    )
    _validate(bundle)
    return bundle
