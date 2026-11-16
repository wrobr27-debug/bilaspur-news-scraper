"""Publishing to `localnews.developerbilaspur.in`.

The site is WordPress with the tagDiv "Newspaper" theme plus a custom
`lnb-` component set. This package renders a fact sheet into that
template and posts it through the REST API.

Nothing here publishes automatically: a post is created as a **draft**,
and promoting it to published is a separate, explicit call.
"""

from .fact_sheet import (
    FactSheetModel,
    extract_facts,
    is_sensitive,
    to_fact_sheet,
)
from .featured_image import alt_text_for, render_featured_image
from .pipeline import (
    PublishResult,
    build_post_payload,
    check_facts,
    publish_from_transcript,
)
from .site_template import (
    CONTEXT_BODY,
    CONTEXT_HEADING,
    FactSheet,
    Keypoint,
    READER_BODY,
    READER_HEADING,
    Section,
    Stat,
    excerpt_text,
    render_post,
    slugify_headline,
)
from .wordpress import PostResult, WordPressClient, WordPressError

__all__ = [
    "CONTEXT_BODY",
    "CONTEXT_HEADING",
    "FactSheet",
    "FactSheetModel",
    "Keypoint",
    "PostResult",
    "PublishResult",
    "READER_BODY",
    "READER_HEADING",
    "Section",
    "Stat",
    "WordPressClient",
    "WordPressError",
    "alt_text_for",
    "build_post_payload",
    "check_facts",
    "excerpt_text",
    "extract_facts",
    "is_sensitive",
    "publish_from_transcript",
    "render_featured_image",
    "render_post",
    "slugify_headline",
    "to_fact_sheet",
]
