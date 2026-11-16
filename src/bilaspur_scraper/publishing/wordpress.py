"""WordPress REST client for `localnews.developerbilaspur.in`.

Reads are open; writes need an **application password** for the `admin`
user. The credential lives in the gitignored `.env` as
`WORDPRESS_APP_PASSWORD` and is never written to code or to the
database.

Two rules are enforced here rather than left to the caller:

* **a post is created as a draft.** `SKILLS.md` §10 requires human
  corroboration for crime, accident, health, legal and election
  material, and §16 lists posting to WordPress as something that must
  not happen without separate authorisation. Drafting keeps the
  pipeline automatic while leaving the publish decision with a human.
  `promote()` is the separate, explicit call that publishes.
* **the featured image is uploaded first** and its id attached, because
  the theme renders `td-post-featured-image` from that id.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://localnews.developerbilaspur.in"


@dataclass
class PostResult:
    """What the WordPress API returned for a created post."""
    post_id: int
    link: str
    slug: str
    status: str
    media_id: Optional[int] = None


class WordPressError(RuntimeError):
    """A WordPress API call failed."""


class WordPressClient:
    """A small REST client for one WordPress site."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *,
                 username: str = "admin",
                 app_password: str | None = None,
                 timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._password = app_password or os.environ.get(
            "WORDPRESS_APP_PASSWORD", "")
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self._password)

    def _auth_header(self) -> str:
        token = base64.b64encode(
            f"{self.username}:{self._password}".encode("utf-8")).decode()
        return f"Basic {token}"

    def _request(self, method: str, path: str, *, data: bytes | None = None,
                 content_type: str | None = None,
                 query: dict | None = None) -> dict:
        url = f"{self.base_url}/wp-json/wp/v2/{path.lstrip('/')}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        headers = {"User-Agent": "BilaspurNewsScraper/1.0",
                   "Accept": "application/json"}
        if self._password:
            headers["Authorization"] = self._auth_header()
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:  # noqa: BLE001
                pass
            raise WordPressError(
                f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise WordPressError(f"{method} {url} -> {exc.reason}") from exc
        if not body:
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    # -- reads ------------------------------------------------------------

    def list_categories(self) -> list[dict]:
        """Every category on the site, read at runtime so a new category
        is picked up without a code change."""
        out: list[dict] = []
        page = 1
        while page <= 5:
            data = self._request("GET", "categories",
                                 query={"per_page": 100, "page": page})
            if not isinstance(data, list):
                break
            out.extend(data)
            if len(data) < 100:
                break
            page += 1
        return out

    def category_ids(self, slugs: list[str]) -> list[int]:
        """The ids for the given category slugs."""
        wanted = {s.strip().lower() for s in slugs if s.strip()}
        if not wanted:
            return []
        found: list[int] = []
        for cat in self.list_categories():
            if str(cat.get("slug", "")).lower() in wanted:
                found.append(int(cat["id"]))
        return found

    def category_slug_map(self) -> dict[str, int]:
        """Every category on the site as `{slug: id}`.

        Read at runtime so a category added to the site is picked up
        without a code change.
        """
        out: dict[str, int] = {}
        for cat in self.list_categories():
            slug = str(cat.get("slug") or "").strip().lower()
            if slug:
                out[slug] = int(cat["id"])
        return out

    def find_post_by_slug(self, slug: str) -> Optional[dict]:
        """An existing post with this slug, if any - used for dedup."""
        data = self._request("GET", "posts",
                             query={"slug": slug, "status": "any",
                                    "per_page": 1, "context": "edit"})
        if isinstance(data, list) and data:
            return data[0]
        return None

    def list_recent_posts(self, limit: int = 20) -> list[dict]:
        data = self._request("GET", "posts",
                             query={"per_page": limit, "orderby": "date",
                                    "order": "desc",
                                    "_fields": "id,date,slug,title,link,status"})
        return data if isinstance(data, list) else []

    # -- writes -----------------------------------------------------------

    def upload_media(self, image_path: Path, *, title: str,
                     alt_text: str, caption: str = "") -> int:
        """Upload the featured image and return its media id."""
        if not image_path.exists():
            raise WordPressError(f"image not found: {image_path}")
        mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        boundary = f"----bilaspur{uuid.uuid4().hex}"
        parts: list[bytes] = []

        def field(name: str, value: str) -> bytes:
            return (f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{value}\r\n").encode("utf-8")

        parts.append(field("title", title))
        parts.append(field("alt_text", alt_text))
        if caption:
            parts.append(field("caption", caption))
        parts.append(
            (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="file"; '
             f'filename="{image_path.name}"\r\n'
             f"Content-Type: {mime}\r\n\r\n").encode("utf-8"))
        parts.append(image_path.read_bytes())
        parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        data = self._request("POST", "media", data=b"".join(parts),
                             content_type=f"multipart/form-data; "
                                         f"boundary={boundary}")
        media_id = data.get("id")
        if not media_id:
            raise WordPressError(f"media upload returned no id: {data}")
        return int(media_id)

    def create_post(self, *, title: str, content: str, excerpt: str,
                    slug: str, categories: list[int],
                    featured_media: int | None = None,
                    status: str = "draft",
                    comment_status: str = "closed",
                    ping_status: str = "closed") -> PostResult:
        """Create the post. Defaults to `draft` - publishing is a separate,
        explicit call."""
        payload = {
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "slug": slug,
            "categories": categories,
            "status": status,
            "comment_status": comment_status,
            "ping_status": ping_status,
        }
        if featured_media:
            payload["featured_media"] = featured_media
        data = self._request("POST", "posts",
                             data=json.dumps(payload).encode("utf-8"),
                             content_type="application/json")
        post_id = data.get("id")
        if not post_id:
            raise WordPressError(f"post creation returned no id: {data}")
        return PostResult(
            post_id=int(post_id),
            link=data.get("link", ""),
            slug=data.get("slug", slug),
            status=data.get("status", status),
            media_id=featured_media,
        )

    def promote(self, post_id: int) -> PostResult:
        """Publish a draft. This is the step that requires a human's
        explicit go-ahead."""
        data = self._request("POST", f"posts/{post_id}",
                             data=json.dumps({"status": "publish"}).encode(),
                             content_type="application/json")
        return PostResult(
            post_id=int(data.get("id", post_id)),
            link=data.get("link", ""),
            slug=data.get("slug", ""),
            status=data.get("status", "publish"),
        )

    def update_post(self, post_id: int, **fields) -> PostResult:
        """Update an existing post."""
        data = self._request("POST", f"posts/{post_id}",
                             data=json.dumps(fields).encode("utf-8"),
                             content_type="application/json")
        return PostResult(
            post_id=int(data.get("id", post_id)),
            link=data.get("link", ""),
            slug=data.get("slug", ""),
            status=data.get("status", ""),
        )
