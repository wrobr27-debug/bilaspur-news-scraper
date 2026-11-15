"""Rule-based relevance - deterministic; CG co-occurrence guard, sensitive gate."""

from __future__ import annotations

from bilaspur_scraper.processing.relevance import KeywordGroups, RelevanceEngine


def test_explicit_bilaspur_with_cg_context_is_high():
    engine = RelevanceEngine()
    result = engine.classify(
        "Bilaspur district launches new civic drive",
        "The Bilaspur administration in Chhattisgarh announced the programme.")
    assert result.score == "high"
    assert "Bilaspur" in result.matched_terms


def test_ambiguous_bilaspur_without_cg_signal_is_unknown():
    # Bilaspur also exists in Himachal Pradesh - no CG signal here.
    result = RelevanceEngine().classify(
        "Bilaspur gets new bus stand",
        "The Himachal town of Bilaspur will see construction start next month.")
    assert result.score == "unknown"


def test_bilaspur_focus_source_corroborates_bare_place_name():
    """A source whose declared scope is Bilaspur city provides the district
    context itself (workbook geographic rule)."""
    engine = RelevanceEngine()
    result = engine.classify(
        "Review of Bilaspur's flyover and railway overbridge",
        "The chief secretary reviewed the pending works and issued "
        "directives to clarify costs.",
        source_coverage="city")
    assert result.score == "high"
    assert "declared Bilaspur coverage" in result.reason


def test_nearby_area_corroborated_by_district_source():
    # 'Kota' alone is ambiguous (Rajasthan) - the district source resolves it.
    engine = RelevanceEngine()
    result = engine.classify(
        "Kota gets new community hall",
        "The panchayat approved construction after the gram sabha meeting.",
        source_coverage="district")
    assert result.score == "high"
    assert "declared Bilaspur coverage" in result.reason


def test_wide_source_still_needs_cg_signal_for_bare_bilaspur():
    """A statewide/regional source keeps the ambiguity guard."""
    result = RelevanceEngine().classify(
        "Bilaspur gets new bus stand",
        "The town will see construction start next month.",
        source_coverage="statewide")
    assert result.score == "unknown"


def test_no_signal_stays_low_even_for_focus_source():
    result = RelevanceEngine().classify(
        "National policy debate continues",
        "Parliament discussed unrelated national matters all week.",
        source_coverage="city")
    assert result.score == "low"


def test_institution_mention_is_high():
    result = RelevanceEngine().classify(
        "SECL coal production update",
        "South Eastern Coalfields Limited reported quarterly figures.")
    assert result.score == "high"


def test_no_signal_is_low():
    result = RelevanceEngine().classify(
        "National policy debate continues",
        "Parliament discussed unrelated national matters all week.")
    assert result.score == "low"


def test_cg_wide_item_is_medium():
    result = RelevanceEngine().classify(
        "Chhattisgarh announces scheme",
        "The state government unveiled a statewide welfare scheme.")
    assert result.score == "medium"


def test_sensitive_topic_is_flagged():
    engine = RelevanceEngine()
    result = engine.classify(
        "Road accident in Bilaspur",
        "A road accident on the Bilaspur highway in Chhattisgarh injured two "
        "people yesterday evening.")
    assert result.sensitive is True
    assert "public safety" in result.matched_topics


def test_db_groups_override_defaults():
    groups = KeywordGroups.from_db_groups([
        ("city watchlist", ["Kankarbagh"]),  # not real, just override check
        ("institutions", ["ZZZ Corp"]),
    ])
    engine = RelevanceEngine(groups)
    result = engine.classify("ZZZ Corp opens unit", "ZZZ Corp expanded.")
    assert result.score == "high"
    assert "ZZZ Corp" in result.matched_terms
    # defaults still carry CG signals
    assert groups.cg_signals
