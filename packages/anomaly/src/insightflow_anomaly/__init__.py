"""Explainable anomaly detection.

Detection is always relative to a stated baseline, always names its method, and
always carries the method's limitation alongside the result.
"""

from insightflow_anomaly.detector import (
    MIN_GROUP_SIZE,
    MIN_MULTIVARIATE_SAMPLE,
    MIN_ROLLING_WINDOW,
    MIN_UNIVARIATE_SAMPLE,
    AnomalyDetector,
    DetectionRequest,
)

__all__ = [
    "MIN_GROUP_SIZE",
    "MIN_MULTIVARIATE_SAMPLE",
    "MIN_ROLLING_WINDOW",
    "MIN_UNIVARIATE_SAMPLE",
    "AnomalyDetector",
    "DetectionRequest",
    "detect_anomalies",
]


def detect_anomalies(frame, snapshot, request: DetectionRequest):
    """Run anomaly detection, choosing the method from the data when unspecified."""
    return AnomalyDetector().detect(frame, snapshot, request)
