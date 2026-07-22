"""Data-quality module — the FDE loop: inspect → fix → evaluate broken data."""

from .profiler import profile_dataset
from .cleaner import clean_dataset
from .evaluate import evaluate_quality, quality_score

__all__ = [
    "profile_dataset",
    "clean_dataset",
    "evaluate_quality",
    "quality_score",
]
