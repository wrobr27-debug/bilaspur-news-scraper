"""Path-aware Bilaspur matching for discovery (D19).

Two separate questions, kept separate on purpose:

1. **Does this URL belong to this source's Bilaspur section?**
   `scope_prefix()` answers it: a national publisher's registry URL may
   point at `/chhattisgarh/bilaspur/news`, and only URLs under that
   prefix qualify. A source whose registry URL is its bare homepage has
   no prefix, so its *own* articles are the candidate set.

2. **Is this URL about the Bilaspur district?**
   `match_bilaspur()` answers it, by looking at the **path**, never at
   free text. There is a town called Bilaspur in Himachal Pradesh, so
   a naive substring search would pull in the wrong state.

The rules, in order:

* the URL is under the source's own Bilaspur section -> a match, and
  the article path must still name the district or one of its
  localities;
* the path names the district (`/bilaspur/`, `/bilaspur-news/`);
* the path names a Chhattisgarh edition *and* names Bilaspur;
* the path names a district locality (Kota, Takhatpur, Ratanpur, ...);
* the source is a Bilaspur-local publisher (city/district scope) and
  the URL looks like an article -> a match, because such a publisher's
  own articles are Bilaspur coverage even when the URL does not say so.

Every match records *why*, so a stored article can be audited later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

#: The district's own localities. Used as a path-scoped signal: the word
#: must appear as a path segment, not as free text in a headline.
DISTRICT_LOCALITIES = (
    "bilaspur", "bemetara", "behatar", "kota", "takhatpur", "ratanpur",
    "ratnpur", "masturi", "sarkanda", "koni", "kotent", "gadarwara",
    "pandaria", "pendra", "champa", "sakti", "lormi", "mungeli",
    "manendragarh",
)

#: Path segments that identify the Chhattisgarh edition of a publisher.
CHHATTISGARH_SEGMENTS = ("chhattisgarh", "cg", "chhattisgarh-news")

#: A path segment that is exactly one of the district's localities.
_LOCALITY_RE = re.compile(
    r"(?:^|/)(?:" + "|".join(re.escape(t) for t in DISTRICT_LOCALITIES) + r")"
    r"(?:-|/|$)", re.IGNORECASE)


@dataclass(frozen=True)
class MatchResult:
    """Why a URL was (or was not) considered Bilaspur-relevant."""
    matched: bool
    reason: str = ""
    terms: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.matched


#: Path segments that identify a state or region other than
#: Chhattisgarh. A publisher nests its sections this way
#: (`/local/uttar-pradesh/rampur/...`), so a URL under one of these is
#: that state's coverage, not ours.
_OTHER_STATE_SEGMENTS = (
    "uttar-pradesh", "uttarakhand", "madhya-pradesh", "himachal-pradesh",
    "himachal", "rajasthan", "bihar", "jharkhand", "maharashtra", "gujarat",
    "madhya", "punjab", "haryana", "kerala", "karnataka", "tamil-nadu",
    "andhra-pradesh", "telangana", "odisha", "west-bengal", "assam",
    "arunachal-pradesh", "manipur", "meghalaya", "mizoram", "nagaland",
    "sikkim", "tripura", "goa", "delhi", "jammu-and-kashmir",
    "himachal-pradesh", "uttarakhand", "baloda-garh", "morbi",
)


def _names_other_state(section_part: str) -> bool:
    """True when the path segment names a state other than Chhattisgarh."""
    lowered = section_part.lower()
    return any(seg in lowered for seg in _OTHER_STATE_SEGMENTS)


def scope_prefix(source_url: str) -> str | None:
    """The path prefix that scopes a source to Bilaspur, if any.

    A national publisher's registry URL may point at its Bilaspur
    section (`/chhattisgarh/bilaspur/news`). That section - not the
    whole site - is the source's coverage, and its site-wide sitemap
    must be filtered down to URLs under that prefix.

    Publishers are inconsistent about where the article slug sits:
    Nai Dunia's section is `/chhattisgarh/bilaspur` but its articles are
    `/chhattisgarh/bilaspur-<slug>` - a *sibling* of the section, not a
    child. So the prefix keeps the parent directory and names the
    segment, rather than pinning the whole path.

    Returns the prefix (without a trailing slash) or None when the
    source's own URL does not narrow it down.
    """
    parts = urlsplit(source_url)
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None

    for i, seg in enumerate(segments):
        low = seg.lower()
        if low == "bilaspur" or low.startswith("bilaspur-"):
            return _prefix_from(segments, i)
        if low in DISTRICT_LOCALITIES:
            return _prefix_from(segments, i)
    for i, seg in enumerate(segments):
        low = seg.lower()
        if low in CHHATTISGARH_SEGMENTS or low.startswith("chhattisgarh-"):
            return _prefix_from(segments, i)
    return None


def _prefix_from(segments: list[str], index: int) -> str:
    """Build the section prefix for the segment at `index`.

    Publisher wrappers (`/category`, `/city`, `/local`, `/state`) sit in
    front of the real path but never change which section it is, so
    they are dropped from the prefix.
    """
    wrappers = ("category", "categories", "city", "local", "state", "region",
                "topic", "section")
    head = list(segments[:index + 1])
    while len(head) > 1 and head[0].lower() in wrappers:
        head.pop(0)
    return "/" + "/".join(head)


def _looks_like_article(path: str) -> bool:
    """True when the path looks like an article URL, not a section page."""
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    last = segments[-1].lower()
    if last in ("", "index.html", "index.htm", "index.php",
                "wp-login.php", "login", "feed", "rss.xml", "sitemap.xml"):
        return False
    if last.endswith((".html", ".htm", ".php")):
        return True
    # a numeric article id (`/2562/`)
    if re.fullmatch(r"\d{4,}", last):
        return True
    # a slug: several words joined by dashes
    if "-" in last and len(last) > 12:
        return True
    # a dated path (/2026/11/slug)
    if len(segments) >= 2 and re.fullmatch(r"\d{4}", segments[-2]):
        return True
    # a nested slug under a known content directory
    if len(segments) >= 2 and segments[-2].lower() in _CONTENT_SEGMENTS:
        return True
    return False


#: Path segments whose children are article-like by construction.
_CONTENT_SEGMENTS = ("news", "story", "stories", "article", "articles",
                     "post", "posts", "notice", "notices", "press",
                     "press-release", "content", "blog", "vlogs", "video",
                     "videos")


def match_bilaspur(url: str, *, host: str | None = None,
                   allow_localities: bool = True,
                   source_scope: str | None = None,
                   prefix: str | None = None) -> MatchResult:
    """Decide whether a URL belongs to Bilaspur coverage.

    `host` restricts the match to one publisher: a URL on another
    domain is never a match, even if its path says "bilaspur".

    `prefix` is the source's Bilaspur section path (from `scope_prefix`).
    When the source has one, only URLs under it qualify - this is what
    stops a national publisher's site-wide sitemap from yielding
    articles from other states.

    `source_scope` is the source's declared registry coverage. A source
    that exists *to* cover Bilaspur (a district portal, a Bilaspur-only
    publisher) publishes articles whose URLs need not name the district -
    `/press/notice-1.html` is perfectly good Bilaspur coverage.
    """
    parts = urlsplit(url)
    if not parts.path or parts.path == "/":
        # A query-string URL (`?p=20080`) is how WordPress serves posts.
        # It carries no path signal, so only a source that exists to
        # cover Bilaspur can claim it.
        if source_scope in ("city", "district"):
            return MatchResult(True,
                               "source is a declared Bilaspur city/district "
                               "source (query-string URL)",
                               ("source_scope",))
        return MatchResult(False, "no path")

    if host:
        if parts.netloc.lower() != host.lower():
            return MatchResult(False, f"different host ({parts.netloc})")

    path = parts.path.lower()
    segments = [s for s in path.split("/") if s]

    # 0. the source has a Bilaspur section: the URL must name the same
    #    segment somewhere in its path, under a compatible parent.
    #    Publishers nest differently - bhaskar puts articles under
    #    `/local/chhattisgarh/bilaspur/news/...` while its registry URL
    #    is `/chhattisgarh/bilaspur/` - so the parent directory may gain
    #    or lose a level. The *segment* is what identifies the section.
    if prefix:
        parent, _, segment = prefix.rpartition("/")
        segment = segment.lower()
        parent = parent.lower()
        segs = [s.lower() for s in segments]
        # The section segment may appear exactly (`/bilaspur/...`) or as
        # the head of a compound slug (`/bilaspur-minor-girl-...`).
        idx = next((i for i, s in enumerate(segs)
                    if s == segment or s.startswith(segment + "-")), None)
        if idx is None:
            return MatchResult(False, f"outside the source's section {prefix}")
        if parent and parent != "/" and not path.startswith(parent + "/"):
            # the parent differs - allow it only when the extra level is
            # a known publisher wrapper such as `/local` or `/category`
            wrappers = ("/local", "/category", "/city", "/state", "/region")
            if not any(path.startswith(w + "/") or parent.startswith(w)
                       for w in wrappers):
                return MatchResult(False,
                                   f"outside the source's section {prefix}")
        # The article itself must be *about* Bilaspur: the district must
        # be named in the section part of the path, not merely inside the
        # article slug (a UP story can be slugged "bilaspur-something").
        section_part = "/".join(segments[:idx + 1])
        rest = "/".join(segments[idx + 1:])
        if "bilaspur" in section_part or "bilaspur" in rest:
            # the slug must not be the only mention when the section part
            # already names a *different* state
            if _names_other_state(section_part):
                return MatchResult(False,
                                   "path belongs to another state's section")
            return MatchResult(True,
                               f"under the source's Bilaspur section {prefix}",
                               ("bilaspur",))
        if allow_localities:
            local = _LOCALITY_RE.search(rest)
            if local:
                term = local.group(0).strip("/").split("-")[0]
                return MatchResult(True,
                                   f"district locality in path ({term})",
                                   (term,))
        return MatchResult(False, "no Bilaspur district signal under the "
                                  "source's section")

    # 1. the district path segment itself
    if any(s == "bilaspur" or s.startswith("bilaspur-")
           for s in segments):
        return MatchResult(True, "Bilaspur path segment", ("bilaspur",))

    # 2. a Chhattisgarh edition whose article slug names Bilaspur
    in_cg = any(s in CHHATTISGARH_SEGMENTS or s.startswith("chhattisgarh-")
                for s in segments)
    if in_cg and "bilaspur" in path:
        return MatchResult(True, "Chhattisgarh edition, Bilaspur in path",
                           ("bilaspur",))

    # 3. a district locality as its own path segment
    if allow_localities:
        local = _LOCALITY_RE.search(path)
        if local:
            term = local.group(0).strip("/").split("-")[0]
            return MatchResult(True, f"district locality in path ({term})",
                               (term,))

    # 4. the source itself is a Bilaspur-local publisher, so its own
    #    articles are Bilaspur coverage even when the URL does not say so.
    if source_scope in ("city", "district") and _looks_like_article(path):
        return MatchResult(True,
                           "source is a declared Bilaspur city/district source",
                           ("source_scope",))

    return MatchResult(False, "no Bilaspur district signal in path")
