"""Train-only word and character features with numerical zone weights."""

from django.core.exceptions import ValidationError
from scipy.sparse import csr_matrix
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline

DEFAULT_SETTINGS = {
    "word_features": 20_000,
    "char_features": 30_000,
    "zone_weights": {"title": 2.0, "header": 1.5, "body": 1.0},
}


class ZoneSelector(TransformerMixin, BaseEstimator):
    def __init__(self, zone):
        self.zone = zone

    def fit(self, inputs, y=None):
        return self

    def transform(self, inputs):
        return [str(row.get(self.zone, "")) for row in inputs]


class SafeTfidf(TfidfVectorizer):
    def fit_transform(self, raw_documents, y=None):
        try:
            self.is_empty_ = False
            return super().fit_transform(raw_documents, y)
        except ValueError as exc:
            if "empty vocabulary" not in str(exc):
                raise
            self.is_empty_ = True
            self.vocabulary_ = {}
            return csr_matrix((len(raw_documents), 0), dtype="float64")

    def transform(self, raw_documents):
        if getattr(self, "is_empty_", False):
            return csr_matrix((len(raw_documents), 0), dtype="float64")
        return super().transform(raw_documents)


def build_features(settings=None, *, branches=None):
    import numpy as np

    settings = settings or DEFAULT_SETTINGS
    if set(settings) != set(DEFAULT_SETTINGS) or settings["zone_weights"] != DEFAULT_SETTINGS["zone_weights"]:
        raise ValidationError("Unsupported feature recipe.")
    for key, limit in [("word_features", 20_000), ("char_features", 30_000)]:
        if type(settings[key]) is not int or not 10 <= settings[key] <= limit:
            raise ValidationError("Feature budget is outside the supported range.")
    transformers, weights = [], {}
    for zone, weight in settings["zone_weights"].items():
        for kind in ("word", "char"):
            name = f"{zone}_{kind}"
            vectorizer = SafeTfidf(
                analyzer="word" if kind == "word" else "char_wb",
                ngram_range=(1, 2) if kind == "word" else (3, 5),
                max_features=settings[f"{kind}_features"],
                sublinear_tf=True,
                lowercase=True,
                dtype=np.float64,
            )
            if branches is not None:
                branch = branches[name]
                vectorizer.vocabulary_ = branch["vocabulary"]
                vectorizer.fixed_vocabulary_ = True
                vectorizer.is_empty_ = not bool(branch["vocabulary"])
                if not vectorizer.is_empty_:
                    vectorizer.idf_ = np.asarray(branch["idf"], dtype=np.float64)
            transformers.append((name, Pipeline([("zone", ZoneSelector(zone)), ("vector", vectorizer)])))
            weights[name] = weight
    return FeatureUnion(transformers, transformer_weights=weights, n_jobs=1)


def export_branches(features):
    result = {}
    for name, pipeline in features.transformer_list:
        vectorizer = pipeline.named_steps["vector"]
        result[name] = {
            "vocabulary": {term: int(index) for term, index in vectorizer.vocabulary_.items()},
            "idf": [] if vectorizer.is_empty_ else vectorizer.idf_.tolist(),
        }
    return result
