"""Rule-based Bilaspur relevance (`PLAN.md` §7, `SKILLS.md` §6).

Deterministic; driven by the workbook's Keyword Watchlist. A keyword
match alone is never proof; the Chhattisgarh co-occurrence guard applies
to the ambiguous place name 'Bilaspur' - unless the source's own declared
coverage scope (city/district of Bilaspur) provides that context itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.article import RelevanceScore


@dataclass
class KeywordGroups:
    city: list[str] = field(default_factory=list)
    localities: list[str] = field(default_factory=list)
    nearby: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)
    cg_signals: list[str] = field(default_factory=list)
    sensitive_groups: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "KeywordGroups":
        """Workbook-grounded fallback (Keyword Watchlist sheet)."""
        return cls(
            city=["Bilaspur", "बिलासपुर", "Bilaspur Chhattisgarh",
                  "बिलासपुर छत्तीसगढ़"],
            localities=["Sarkanda", "सरकंडा", "Torwa", "तोरवा", "Vyapar Vihar",
                        "व्यापार विहार", "Seepat", "सीपत", "Koni", "कोनी",
                        "Mopka", "मोपका"],
            nearby=["Kota", "कोटा", "Takhatpur", "तखतपुर", "Masturi", "मस्तूरी",
                    "Ratanpur", "रतनपुर", "Malhar", "मल्हार", "Bilha", "बिल्हा"],
            institutions=["SECL", "CIMS", "GGV", "GGVU", "ABVV", "SECR",
                          "NTPC Sipat", "जिला प्रशासन", "नगर निगम",
                          "बिलासपुर पुलिस"],
            cg_signals=["Chhattisgarh", "छत्तीसगढ़", " CG", "CG "],
            sensitive_groups={
                "public safety": ["accident", "सड़क हादसा", "दुर्घटना", "crime",
                                  "अपराध", "arrest", "गिरफ्तारी", "चोरी", "लूट",
                                  "हत्या", "fire", "आग", "traffic diversion",
                                  "यातायात"],
                "healthcare": ["health alert", "स्वास्थ्य", "hospital", "अस्पताल",
                               "dengue", "डेंगू", "malaria", "मल्हारिया",
                               "मलेरिया", "vaccination", "टीकाकरण"],
                "legal/elections": ["election", "चुनाव", "court", "अदालत",
                                    "case", "मामला", "FIR"],
            },
        )

    @classmethod
    def from_db_groups(cls, rows: list[tuple[str, list[str]]]) -> "KeywordGroups":
        groups = cls.default()
        for name, terms in rows:
            lowered = name.lower()
            if "city" in lowered:
                groups.city = terms or groups.city
            elif "localities" in lowered:
                groups.localities = terms or groups.localities
            elif "nearby" in lowered:
                groups.nearby = terms or groups.nearby
            elif lowered == "institutions":
                groups.institutions = terms or groups.institutions
            elif "public safety" in lowered:
                groups.sensitive_groups["public safety"] = terms
            elif "healthcare" in lowered:
                groups.sensitive_groups["healthcare"] = terms
            elif "civic" in lowered or "government" in lowered:
                pass  # topical groups recorded as matched_topics only
        # CG co-occurrence signal always includes the city terms' district form.
        groups.cg_signals += ["Chhattisgarh", "छत्तीसगढ़"]
        return groups


@dataclass
class RelevanceResult:
    score: RelevanceScore
    reason: str
    matched_terms: list[str]
    matched_topics: list[str]
    sensitive: bool


def _find_terms(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


class RelevanceEngine:
    def __init__(self, groups: KeywordGroups | None = None) -> None:
        self._groups = groups or KeywordGroups.default()

    def classify(self, title: str | None, content: str | None, *,
                 source_coverage: str | None = None) -> RelevanceResult:
        """Classify one item.

        `source_coverage` is the source's declared registry coverage scope.
        For a Bilaspur city/district source, a place-name match is
        corroborated by the publisher's own identity (workbook 'Geographic
        rule': the district is the priority); for wider sources the
        Chhattisgarh co-occurrence guard still applies.
        """
        text = f"{title or ''}\n{content or ''}"
        g = self._groups
        city = _find_terms(text, g.city)
        localities = _find_terms(text, g.localities)
        nearby = _find_terms(text, g.nearby)
        institutions = _find_terms(text, g.institutions)
        cg = _find_terms(text, g.cg_signals)
        matched = city + localities + nearby + institutions
        matched = list(dict.fromkeys(matched))  # stable de-dup

        # Sensitive-topic gate (SKILLS.md §10).
        matched_topics: list[str] = []
        for topic, terms in g.sensitive_groups.items():
            if _find_terms(text, terms):
                matched_topics.append(topic)

        source_ok = source_coverage in ("city", "district")
        cg_ok = bool(cg) or bool(institutions) or bool(localities) or source_ok

        if city or localities:
            if cg_ok:
                if source_ok and not bool(cg):
                    return RelevanceResult(
                        "high",
                        "Bilaspur match corroborated by the source's declared "
                        "Bilaspur coverage",
                        matched, matched_topics, bool(matched_topics))
                return RelevanceResult(
                    "high",
                    "explicit Bilaspur city/locality match with Chhattisgarh "
                    "or district context",
                    matched, matched_topics, bool(matched_topics))
            return RelevanceResult(
                "unknown",
                "ambiguous place name; no Chhattisgarh signal",
                matched, matched_topics, bool(matched_topics))
        if nearby:
            if cg_ok:
                if source_ok and not (bool(cg) or bool(localities)):
                    return RelevanceResult(
                        "high",
                        "nearby-area item corroborated by the source's declared "
                        "Bilaspur coverage",
                        matched, matched_topics, bool(matched_topics))
                return RelevanceResult(
                    "high",
                    "nearby-area item with district context",
                    matched, matched_topics, bool(matched_topics))
            return RelevanceResult(
                "unknown",
                "ambiguous nearby-area name; no Chhattisgarh signal",
                matched, matched_topics, bool(matched_topics))
        if institutions:
            return RelevanceResult(
                "high",
                "Bilaspur district institution explicitly mentioned",
                matched, matched_topics, bool(matched_topics))
        if cg:
            return RelevanceResult(
                "medium",
                "Chhattisgarh-wide item; district impact not explicit",
                matched, matched_topics, bool(matched_topics))
        return RelevanceResult(
            "low",
            "no Bilaspur or Chhattisgarh signal in content",
            matched, matched_topics, bool(matched_topics))
