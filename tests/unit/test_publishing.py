"""Tests for the WordPress publishing layer.

Three things matter and are easy to get wrong:

* the rendered body must match the live site's structure exactly, or
  the theme will not style it;
* a fact sheet's specifics must be checked against the transcript
  before anything is posted;
* the categories chosen must come from the site's own list, and a
  story about the police must not be filed as crime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from bilaspur_scraper.publishing.pipeline import (  # noqa: E402
    choose_categories,
    check_facts,
)
from bilaspur_scraper.publishing.site_template import (  # noqa: E402
    CONTEXT_BODY,
    READER_BODY,
    FactSheet,
    Keypoint,
    Section,
    Stat,
    excerpt_text,
    render_post,
    slugify_headline,
)

#: The site's real category slugs, as read from the live REST API.
SITE_SLUGS = {
    "bilaspur": 32, "breaking": 70, "crime": 38, "traffic": 48,
    "health": 40, "politics": 36, "local-events": 45, "nagar-nigam": 47,
    "district-administration": 44, "jobs-careers": 41, "education": 39,
    "business": 37, "sports": 43, "entertainment": 42,
}


def _facts(**kw) -> FactSheet:
    base = dict(
        headline="बिलासपुर: सीपत वन परिक्षेत्र में जलभराव की शिकायत",
        lede="प्रशासन ने 12 नवंबर को सुबह 9 बजे कार्रवाई शुरू की थी।",
        keypoints=(Keypoint(label="क्या है मामला:", detail="दफ्तर में पानी जमा हो गया।"),),
        stats=(Stat(value="1000 मीटर", label="लंबा घाट तैयार है"),),
        sections=(Section(heading="क्या है तैयारी", paragraphs=("प्रशासन ने कार्रवाई की।",)),),
        source="बिलासपुर ज़िला प्रशासन",
    )
    base.update(kw)
    return FactSheet(**base)


# --------------------------------------------------------------------------
# the rendered body
# --------------------------------------------------------------------------

def test_rendered_body_has_every_component_the_theme_expects():
    html = render_post(_facts())
    for marker in ('class="lnb-lede"', 'class="lnb-keypoints"',
                   'class="lnb-stats"', 'class="lnb-stat-value"',
                   'class="lnb-stat-label"', 'class="lnb-context"',
                   'class="lnb-reader"', "<h2>", "<p>"):
        assert marker in html, f"missing {marker}"


def test_the_two_fixed_boxes_are_inserted_verbatim():
    html = render_post(_facts())
    assert CONTEXT_BODY in html
    assert READER_BODY in html


def test_the_source_line_carries_the_attribution():
    html = render_post(_facts(source="बिलासपुर ज़िला प्रशासन"))
    assert "स्रोत: बिलासपुर ज़िला प्रशासन" in html


def test_the_ad_slots_are_present_when_asked_for():
    with_ads = render_post(_facts(), include_ads=True)
    without = render_post(_facts(), include_ads=False)
    assert "lnb-ad-top" in with_ads
    assert "lnb-ad-top" not in without


def test_the_body_escapes_html():
    """A headline with markup must not inject it into the page."""
    facts = _facts(headline="<script>alert(1)</script> बिलासपुर")
    html = render_post(facts)
    assert "<script>alert(1)</script>" not in html
    assert "<script>" in html


def test_excerpt_is_plain_text():
    facts = _facts()
    assert excerpt_text(facts) == facts.lede
    assert "<" not in excerpt_text(facts)


# --------------------------------------------------------------------------
# slugs
# --------------------------------------------------------------------------

def test_slug_folds_to_ascii():
    assert slugify_headline(
        "Chhath Puja 2026: अरपा तट पर बिलासपुर को 50 हज़ार श्रद्धालुओं के उमड़ने "
        "की तैयारी").startswith("chhath-puja-2026")


def test_slug_for_a_fully_devanagari_headline_is_still_unique():
    """There are no ASCII characters to fold, so a stable fallback is
    used rather than an empty slug."""
    a = slugify_headline("बिलासपुर में छठ महापर्व")
    b = slugify_headline("बिलासपुर में छठ पूजा")
    assert a and b
    assert a != b
    assert a.startswith("bilaspur-news-")


def test_slug_is_not_a_generic_word():
    assert slugify_headline("News") != "news"


# --------------------------------------------------------------------------
# fact checking
# --------------------------------------------------------------------------

def test_a_faithful_sheet_passes():
    transcript = ("बिलासपुर। सीपत वन परिक्षेत्र में वन विभाग के सरकारी दफ्तर "
                  "में जलभराव की शिकायत मिली है। प्रशासन ने 12 नवंबर को सुबह "
                  "9 बजे कार्रवाई शुरू की थी। 1000 मीटर लंबा घाट तैयार है।")
    result = check_facts(_facts(), transcript)
    assert result["ok"], result["flags"]


def test_an_invented_number_is_flagged():
    transcript = ("बिलासपुर। सीपत में जलभराव की शिकायत। प्रशासन ने 12 नवंबर "
                  "को सुबह 9 बजे कार्रवाई शुरू की थी। 1000 मीटर लंबा घाट तैयार है।")
    facts = _facts(stats=(Stat(value="45 लाख रुपए", label="नुकसान"),))
    result = check_facts(facts, transcript)
    assert not result["ok"]
    assert any("45" in f for f in result["flags"])


def test_an_invented_name_is_flagged():
    transcript = "बिलासपुर। सीपत में जलभराव की शिकायत।"
    facts = _facts(sections=(Section(heading="बयान",
                                    paragraphs=("कलेक्टर Rajesh Verman ने कहा।",)),))
    result = check_facts(facts, transcript)
    assert not result["ok"]
    assert any("Rajesh" in f for f in result["flags"])


def test_a_label_is_not_flagged_as_a_claim():
    """`क्या है मामला:` is the writer's structure, not a fact."""
    transcript = ("बिलासपुर। सीपत में जलभराव की शिकायत। प्रशासन ने 12 नवंबर को "
                  "सुबह 9 बजे कार्रवाई शुरू की थी। 1000 मीटर लंबा घाट तैयार है।")
    facts = _facts(keypoints=(Keypoint(label="क्या है मामला:",
                                       detail="दफ्तर में पानी जमा हो गया।"),))
    result = check_facts(facts, transcript)
    assert result["ok"], result["flags"]


# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------

def test_bilaspur_is_always_selected():
    facts = _facts(headline="अंबिकापुर में कबरा हाथी", lede="जंगल में हाथी।")
    ids = choose_categories(facts, available_slugs=SITE_SLUGS)
    assert SITE_SLUGS["bilaspur"] in ids


def test_a_crime_story_is_filed_under_crime():
    facts = _facts(
        headline="बिलासपुर: सब्जी मंडी में पार्षद को मारपीट",
        lede="आरोपी को गिरफ्तार किया गया।",
        sections=(Section(heading="मामला", paragraphs=("पुलिस ने मुकदमा दर्ज किया।",)),))
    ids = choose_categories(facts, available_slugs=SITE_SLUGS)
    assert SITE_SLUGS["crime"] in ids


def test_a_traffic_story_is_not_filed_under_crime():
    """A traffic story quotes the police too - only the act decides."""
    facts = _facts(
        headline="बिलासपुर में सड़क दुर्घटना",
        lede="ट्रैफिक पुलिस ने कार्रवाई की।",
        sections=(Section(heading="हादसा", paragraphs=("तीन वाहन टकराए।",)),))
    ids = choose_categories(facts, available_slugs=SITE_SLUGS)
    assert SITE_SLUGS["traffic"] in ids
    assert SITE_SLUGS["crime"] not in ids


def test_a_slug_the_site_does_not_have_is_skipped():
    facts = _facts()
    ids = choose_categories(facts, available_slugs={"bilaspur": 32})
    assert ids == [32]


def test_no_available_categories_falls_back_to_nothing():
    facts = _facts()
    assert choose_categories(facts, available_slugs={}) == []
