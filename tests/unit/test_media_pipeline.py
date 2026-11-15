"""Tests for the video -> transcript -> article pipeline.

The fact-checker is the piece that matters most: it is what stands
between a fluent LLM paragraph and an invented fact reaching the
database. These tests prove it catches the three ways a model invents
things - a number, a name, a place - and does not flag a faithful
summary.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from media_pipeline import fact_check  # noqa: E402

TRANSCRIPT = (
    "बिलासपुर। सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में जलभराव की "
    "शिकायत मिली है। स्थानीय लोगों ने बताया कि कर्मचारियों की लापरवाही के कारण "
    "पानी जमा हो गया। वन परिक्षेत्र अधिकारी ने कहा कि जल निकासी की कार्रवाई शुरू "
    "कर दी गई है।"
)

FAITHFUL = (
    "बिलासपुर: सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में जलभराव की "
    "शिकायत मिली है। स्थानीय लोगों ने कर्मचारियों की लापरवाही से पानी जमा होने की "
    "बात कही। वन परिक्षेत्र अधिकारी ने कहा कि जल निकासी की कार्रवाई शुरू कर दी "
    "गई है।"
)


def test_faithful_summary_passes():
    result = fact_check(FAITHFUL, TRANSCRIPT)
    assert result["ok"], result["flags"]
    assert result["flags"] == []


def test_invented_number_is_flagged():
    article = (FAITHFUL + " इस नुकसान की भरपाई के लिए 45 लाख रुपए की मांग की "
                      "गई है।")
    result = fact_check(article, TRANSCRIPT)
    assert not result["ok"]
    assert any("45" in f for f in result["flags"])


def test_invented_person_name_is_flagged():
    article = (FAITHFUL + " कलेक्टर Rajesh Verman ने जांच के निर्देश दिए हैं।")
    result = fact_check(article, TRANSCRIPT)
    assert not result["ok"]
    assert any("Rajesh" in f for f in result["flags"])
    assert any("Verman" in f for f in result["flags"])


def test_invented_place_is_flagged():
    article = FAITHFUL + " रायपुर में इसी तरह की घटना पिछले साल हुई थी।"
    result = fact_check(article, TRANSCRIPT)
    assert not result["ok"]
    assert any("रायपुर" in f for f in result["flags"])


def test_invented_devanagari_name_after_a_title_is_flagged():
    """A title followed by a name is a reliable proper-noun signal."""
    article = FAITHFUL + " कलेक्टर रमेश वर्मा ने जांच के निर्देश दिए हैं।"
    result = fact_check(article, TRANSCRIPT)
    assert not result["ok"]
    # the name word itself must be absent from the transcript
    assert any("रमेश" in f for f in result["flags"])


def test_a_reordered_faithful_name_is_not_flagged():
    """The brief may reorder the source ("मंडल अध्यक्ष पर" where the
    transcript said "अध्यक्ष"), so the check must be word-level."""
    source = ("बिलासपुर। भाजपा की अनुसूचित जाति मोर्चा के तत्कालिक अध्यक्ष पर "
              "आरोप लगा था। पुलिस ने इस मामले में कार्रवाई की थी और आरोपियों को "
              "पकड़ा गया था।")
    article = ("बिलासपुर: भाजपा की अनुसूचित जाति मोर्चा के मंडल अध्यक्ष पर आरोप "
               "लगा था। पुलिस ने इस मामले में कार्रवाई की थी और आरोपियों को "
               "पकड़ा गया था।")
    result = fact_check(article, source)
    assert result["ok"], result["flags"]


def test_a_faithful_summary_is_not_flagged_by_the_phrase_check():
    """The phrase check must not fire on ordinary prose or on words that
    span a sentence boundary."""
    article = ("बिलासपुर। सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में "
               "जलभराव की शिकायत मिली है। लगभग 100 मीटर की दूरी पर घटनास्थल है।")
    source = ("बिलासपुर। सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर में "
              "जलभराव की शिकायत मिली है। लगभग 100 मीटर की दूरी पर घटनास्थल है।")
    result = fact_check(article, source)
    assert result["ok"], result["flags"]


def test_short_article_is_flagged():
    result = fact_check("बिलासपुर में जलभराव की शिकायत।", TRANSCRIPT)
    assert not result["ok"]
    assert any("too short" in f for f in result["flags"])


def test_a_summary_may_be_much_shorter_than_its_source():
    """A news bulletin repeats itself and contains ad breaks, so a short
    faithful brief is normal - it must not be flagged as truncated."""
    long_source = TRANSCRIPT * 12
    result = fact_check(FAITHFUL, long_source)
    assert result["ok"], result["flags"]


def test_numbers_that_are_in_the_transcript_are_allowed():
    article = ("बिलासपुर: घटनास्थल थाने से लगभग 100 मीटर की दूरी पर है और "
               "शाम 8:30 बजे बताया गया कि जल निकासी शुरू हो गई है।")
    source = ("बिलासपुर। घटनास्थल थाने से लगभग 100 मीटर की दूरी पर है। शाम 8:30 "
              "बजे जल निकासी शुरू हो गई है।")
    result = fact_check(article, source)
    assert result["ok"], result["flags"]
