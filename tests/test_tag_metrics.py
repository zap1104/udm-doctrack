from importlib.util import find_spec


def test_wilson_requires_independent_support():
    assert find_spec("apps.documents.ml.metrics"), "Qualified ML metrics are missing"
    from apps.documents.ml.metrics import wilson_lower

    assert wilson_lower(0, 0) is None
    assert wilson_lower(95, 100) < 0.95
    assert wilson_lower(100, 100) >= 0.95
