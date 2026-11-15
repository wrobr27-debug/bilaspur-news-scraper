"""Entity-resolution aliases (`PLAN.md` §5).

Aliases come only from workbook evidence:
  1. Publisher names containing ' / ' (e.g. 'Takhatpur Ki Bhadas /
     Bilaspur Ki Bhadas') - each additional part becomes an alias.
  2. Additional Monitoring Targets rows whose normalized name matches an
     institution (e.g. 'Guru Ghasidas Vishwavidyalaya (GGV)',
     'Bilaspur District Administration / Collectorate').
Nothing is invented.
"""

from __future__ import annotations

import re

from ..schemas.source import SourceAlias

_PARENS = re.compile(r"\([^)]*\)")
_PARENS_CONTENT = re.compile(r"\(([^)]*)\)")


def normalize_name(name: str) -> str:
    """Normalized comparison signature: lowercase, drop parentheticals and
    ' / ' suffixes, tokenize, and sort so word order does not matter."""
    base = name.split("/")[0]
    base = _PARENS.sub(" ", base)
    tokens = re.findall(r"[a-z0-9]+", base.lower())
    return " ".join(sorted(tokens))


def clean_alias(name: str) -> str:
    """Alias text for storage: parentheticals dropped, trimmed."""
    cleaned = _PARENS.sub(" ", name).strip()
    return re.sub(r"\s{2,}", " ", cleaned)


def build_publisher_name_aliases(display_name: str, source_id: str) -> list[SourceAlias]:
    """Every ' / ' part after the first becomes an alias of the canonical row."""
    parts = [p.strip() for p in display_name.split("/") if p.strip()]
    aliases: list[SourceAlias] = []
    for part in parts[1:]:
        aliases.append(SourceAlias(alias=part, source_id=source_id))
    return aliases


def build_publisher_name_aliases_from(records) -> list[SourceAlias]:
    """Apply publisher-name splitting over loaded `SourceRecord`s."""
    aliases: list[SourceAlias] = []
    for record in records:
        aliases.extend(
            build_publisher_name_aliases(record.source_name, record.source_id)
        )
    return aliases


def acronym_aliases(name: str) -> list[str]:
    """Extract uppercase acronyms from parentheticals, e.g.
    'Guru Ghasidas Vishwavidyalaya (GGV)' -> ['GGV'].

    Acronyms appear verbatim in the workbook (AMT rows and the Keyword
    Watchlist institutions group), so they are workbook-grounded aliases.
    """
    found: list[str] = []
    for content in _PARENS_CONTENT.findall(name):
        token = content.strip()
        if len(token) >= 2 and token.upper() == token and any(c.isalpha() for c in token):
            if token not in found:
                found.append(token)
    return found


def build_backlog_aliases(backlog_names: list[str],
                          institutions: list[tuple[str, str]]) -> list[SourceAlias]:
    """Match Additional Monitoring Targets names to institution rows.

    `institutions` is a list of (display_name, source_id). A backlog row
    becomes an alias when its normalized signature equals the normalized
    institution signature; parenthetical acronyms become aliases too.
    """
    normalized_index = {
        normalize_name(display): source_id for display, source_id in institutions
    }
    aliases: list[SourceAlias] = []
    seen: set[str] = set()

    def add(alias: str, source_id: str) -> None:
        pair = (alias, source_id)
        if pair not in seen:
            seen.add(pair)
            aliases.append(SourceAlias(alias=alias, source_id=source_id))

    for target_name in backlog_names:
        for part in (p.strip() for p in target_name.split("/")):
            key = normalize_name(part)
            source_id = normalized_index.get(key)
            if source_id is None:
                continue
            alias_text = clean_alias(part)
            if alias_text != institutions_name_for(institutions, source_id):
                add(alias_text, source_id)
            for acronym in acronym_aliases(part):
                if acronym != institutions_name_for(institutions, source_id):
                    add(acronym, source_id)
    return aliases


def institutions_name_for(institutions: list[tuple[str, str]], source_id: str) -> str:
    for display, sid in institutions:
        if sid == source_id:
            return display
    return ""
