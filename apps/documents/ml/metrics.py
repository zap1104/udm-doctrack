"""Null-safe independent-family metrics. No predictions means no precision."""

import math
from statistics import NormalDist


def wilson_lower(successes, trials, *, confidence=0.95):
    if trials == 0:
        return None
    if not 0 <= successes <= trials or not 0 < confidence < 1:
        raise ValueError("Invalid interval inputs")
    z = NormalDist().inv_cdf(confidence)  # one-sided lower confidence bound
    p = successes / trials
    return (p + z * z / (2 * trials) - z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))) / (
        1 + z * z / trials
    )


def score_metrics(labels, probabilities, threshold):
    known = [(int(y), float(p)) for y, p in zip(labels, probabilities, strict=True) if y is not None]
    predicted = [(y, p) for y, p in known if p >= threshold]
    tp = sum(y for y, _ in predicted)
    positives = sum(y for y, _ in known)
    precision = tp / len(predicted) if predicted else None
    recall = tp / positives if positives else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else 0.0
    clipped = [(y, min(1 - 1e-12, max(1e-12, p))) for y, p in known]
    bins = []
    for i in range(10):
        values = [(y, p) for y, p in known if i / 10 <= p < (i + 1) / 10 or (i == 9 and p == 1)]
        if values:
            bins.append(
                {
                    "lower": i / 10,
                    "count": len(values),
                    "mean_score": sum(p for _, p in values) / len(values),
                    "observed": sum(y for y, _ in values) / len(values),
                }
            )
    return {
        "known": len(known),
        "positives": positives,
        "predictions": len(predicted),
        "true_positives": tp,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "coverage": len(predicted) / len(known) if known else 0,
        "abstention": 1 - len(predicted) / len(known) if known else 1,
        "wilson_lower": wilson_lower(tp, len(predicted)),
        "brier": sum((y - p) ** 2 for y, p in known) / len(known) if known else None,
        "log_loss": -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for y, p in clipped) / len(clipped)
        if clipped
        else None,
        "reliability_bins": bins,
    }
