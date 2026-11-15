"""Script-based language detection (D12) - deterministic, offline."""

from __future__ import annotations

from bilaspur_scraper.processing.language import detect_language

HINDI_TEXT = (
    "बिलासपुर में आज एक महत्वपूर्ण बैठक संपन्न हुई। जिला प्रशासन ने नए निर्देश "
    "जारी किए और नगर निगम ने सफाई अभियान शुरू किया। स्थानीय निवासियों ने इस "
    "फैसले का स्वागत किया और आगे की कार्रवाई की जानकारी दी गई। यह खबर पूरे "
    "छत्तीसगढ़ में चर्चित रहेगी।"
)

ENGLISH_TEXT = (
    "The Bilaspur district administration announced a new cleanliness drive "
    "on Monday. Officials said the campaign will run across all wards and "
    "residents can report issues through the municipal helpline. Local "
    "businesses welcomed the decision and promised cooperation. The drive "
    "is expected to continue through the end of the month."
)

# ~10% Devanagari: mixed, but no script reaches its dominance threshold.
MOSTLY_ENGLISH_WITH_SOME_HINDI = (
    ENGLISH_TEXT + "\n" + "बिलासपुर में बैठक हुई।"
)


def test_hindi_script_is_classified_hi():
    assert detect_language(HINDI_TEXT) == "hi"


def test_latin_script_is_classified_en():
    assert detect_language(ENGLISH_TEXT) == "en"


def test_genuinely_mixed_text_is_und():
    assert detect_language(MOSTLY_ENGLISH_WITH_SOME_HINDI) == "und"


def test_weak_evidence_is_und():
    assert detect_language("Bilaspur news") == "und"


def test_empty_text_is_und():
    assert detect_language("") == "und"
    assert detect_language(None) == "und"
