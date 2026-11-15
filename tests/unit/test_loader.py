from bilaspur_scraper.registry import loader
from bilaspur_scraper.schemas.enums import (
    ActivityStatus,
    LifecycleState,
    SourceType,
    SourceVerificationStatus,
    UrlKind,
)


def by_id(records, source_id):
    return next(r for r in records if r.source_id == source_id)


def test_media_rows_are_classified(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    ids = {r.source_id for r in result.sources}

    assert "the-bilasa-times" in ids
    assert "takhatpur-ki-bhadas" in ids
    assert "nai-dunia-bilaspur" in ids
    assert "amar-ujala-bilaspur" in ids
    assert "haribhoomi-bilaspur" in ids
    assert "dainik-bhaskar" in ids
    assert "inh-news" in ids
    assert "sindhu-youth-media" in ids

    bilasa = by_id(result.sources, "the-bilasa-times")
    assert bilasa.url_kind == UrlKind.HOMEPAGE
    assert bilasa.lifecycle_state == LifecycleState.CANDIDATE
    assert bilasa.source_type == SourceType.PUBLISHER_LOCAL
    assert bilasa.source_verification_status == SourceVerificationStatus.PUBLISHER_LINKED
    assert bilasa.activity_status == ActivityStatus.ACTIVE_UNKNOWN_CADENCE
    assert bilasa.enabled is False
    assert bilasa.platform.value == "website"

    stub = by_id(result.sources, "sindhu-youth-media")
    assert stub.url_kind == UrlKind.SEARCH_STUB
    assert stub.lifecycle_state == LifecycleState.EXCLUDED
    assert stub.activity_status == ActivityStatus.NOT_COLLECTABLE_VIA_CURRENT_METHOD
    assert stub.enabled is False


def test_nothing_is_ever_enabled_or_verified_by_import(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    assert all(r.enabled is False for r in result.sources)
    assert all(r.lifecycle_state is not LifecycleState.ENABLED for r in result.sources)
    assert all(r.lifecycle_state is not LifecycleState.VERIFIED for r in result.sources)
    assert all(r.lifecycle_state is not LifecycleState.VERIFICATION_PENDING
               for r in result.sources)


def test_url_kinds(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    assert by_id(result.sources, "nai-dunia-bilaspur").url_kind == UrlKind.SECTION
    assert by_id(result.sources, "amar-ujala-bilaspur").url_kind == UrlKind.VIDEO_SECTION
    assert by_id(result.sources, "haribhoomi-bilaspur").url_kind == UrlKind.TOPIC
    assert by_id(result.sources, "dainik-bhaskar").url_kind == UrlKind.HOMEPAGE
    assert by_id(result.sources, "inh-news").url_kind == UrlKind.AGGREGATOR


def test_institutions(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    da = by_id(result.sources, "district-administration-bilaspur")
    assert da.source_type == SourceType.DISTRICT_GOVERNMENT
    assert da.url_kind == UrlKind.HOMEPAGE
    assert da.coverage_scope.value == "district"
    assert da.source_verification_status == SourceVerificationStatus.VERIFIED_OFFICIAL
    assert da.lifecycle_state == LifecycleState.CANDIDATE

    deo = by_id(result.sources, "district-election-officer-bilaspur")
    assert deo.url_kind == UrlKind.SUB_PATH

    secl = by_id(result.sources, "south-eastern-coalfields-limited-secl")
    assert secl.source_type == SourceType.PSU

    ggu = by_id(result.sources, "guru-ghasidas-vishwavidyalaya")
    assert ggu.source_type == SourceType.CENTRAL_UNIVERSITY


def test_aliases_from_slash_names_and_backlog(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    alias_map = {(a.alias, a.source_id) for a in result.aliases}
    # Second ' / ' part of the publisher name.
    assert ("Bilaspur Ki Bhadas", "takhatpur-ki-bhadas") in alias_map
    # Backlog row 'Bilaspur District Administration / Collectorate' matches
    # the institution after normalization (word order ignored).
    assert ("Bilaspur District Administration", "district-administration-bilaspur") \
        in alias_map
    # Parenthetical acronym from the backlog row becomes an alias.
    assert ("GGV", "guru-ghasidas-vishwavidyalaya") in alias_map


def test_keyword_groups_split_terms(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    groups = {g.group_name: g for g in result.keywords}
    assert "Geography - city" in groups
    city = groups["Geography - city"]
    assert city.terms == ["Bilaspur", "बिलासपुर", "Bilaspur Chhattisgarh"]
    assert len(groups["Geography - localities"].terms) == 4


def test_backlog_and_queue_imported(workbook_path):
    result = loader.load_workbook_records(workbook_path)
    targets = {b.target for b in result.backlog}
    assert "Bilaspur District Administration / Collectorate" in targets
    assert len(result.queue) == 1
    assert result.queue[0].priority == "P0"


def test_quarantine_of_malformed_rows(tmp_path, workbook_path):
    # Add a malformed media row (URL missing) by editing the fixture.
    import openpyxl

    wb = openpyxl.load_workbook(workbook_path)
    ws = wb["Media Publishers"]
    ws.append(["Broken Row", "Bilaspur-focused publisher", "", "", "", "", "",
               "", "", "", ""])
    wb.save(workbook_path)
    result = loader.load_workbook_records(workbook_path)
    reasons = [(q.origin_sheet, q.reason) for q in result.quarantined]
    assert ("Media Publishers", "missing name or URL") in reasons
    assert all(r.source_id != "broken-row" for r in result.sources)
