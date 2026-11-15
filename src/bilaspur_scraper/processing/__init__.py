"""Processing layer: normalization, relevance, deduplication."""

from .dedup import content_hash, normalize_url
from .language import detect_language
from .normalizer import normalize_draft, normalize_whitespace
from .relevance import RelevanceEngine

__all__ = [
    "RelevanceEngine",
    "content_hash",
    "detect_language",
    "normalize_draft",
    "normalize_url",
    "normalize_whitespace",
]
