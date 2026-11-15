"""Script-based language detection (`ARCHITECTURE.md` D12).

Deterministic Unicode script analysis. Predominantly Devanagari -> `hi`,
predominantly Latin -> `en`, genuinely mixed or weak evidence -> `und`.
Original text is always preserved regardless of classification.
"""

from __future__ import annotations

from typing import Literal

LanguageCode = Literal["hi", "en", "und"]

_DEVANAGARI = [("\u0900", "\u097F")]
_LATIN = [("A", "Z"), ("a", "z"), ("\u00C0", "\u024F")]


def _count_scripts(text: str) -> tuple[int, int]:
    devanagari = 0
    latin = 0
    for char in text:
        code = ord(char)
        if any(ord(lo) <= code <= ord(hi) for lo, hi in _DEVANAGARI):
            devanagari += 1
        elif any(ord(lo) <= code <= ord(hi) for lo, hi in _LATIN):
            latin += 1
    return devanagari, latin


def detect_language(text: str | None) -> LanguageCode:
    """Classify by script. Weak evidence (few letters) -> `und`."""
    if not text:
        return "und"
    devanagari, latin = _count_scripts(text)
    total = devanagari + latin
    if total < 50:
        return "und"  # weak evidence - never force a classification
    dev_fraction = devanagari / total
    if dev_fraction >= 0.4:
        # Devanagari is the plurality script (Hindi with English loanwords
        # in Latin script is normal in Indian news).
        return "hi"
    if dev_fraction < 0.05 and latin / total >= 0.9:
        return "en"
    return "und"  # genuinely mixed/uncertain
