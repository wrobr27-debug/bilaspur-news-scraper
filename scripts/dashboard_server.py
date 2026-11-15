"""Read-only dashboard API for the Bilaspur News Scraper.

Serves JSON from the SQLite database over HTTP so a browser dashboard can
monitor collected news. **Read-only by design**: this module never writes
to the database and never runs a collection. It is a monitor, not a
control panel - triggering a crawl stays a deliberate CLI action
(`bilaspur-scraper run ...`), so an open dashboard tab can never cause
outbound traffic to the publishers.

Endpoints:
    GET /api/summary        counts, source states, run history summary
    GET /api/articles       paged articles, filterable
    GET /api/sources        the source registry with live health
    GET /api/runs           recent ingestion runs
    GET /api/article/<id>   one article with its full text

Run it:
    python scripts/dashboard_server.py --port 8766
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_DB = ROOT / "data" / "news.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open the database read-only.

    `file:...?mode=ro` makes SQLite refuse any write, so a bug here can
    never mutate collected data.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _as_dict(value) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# --------------------------------------------------------------------------
# query helpers
# --------------------------------------------------------------------------

def summary(conn: sqlite3.Connection) -> dict:
    def scalar(sql: str, args=()):
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else 0

    sources_by_state = {
        row["lifecycle_state"]: row["n"]
        for row in conn.execute(
            "SELECT lifecycle_state, count(*) AS n FROM sources GROUP BY 1")
    }
    relevance = {
        row["bilaspur_relevance"]: row["n"]
        for row in conn.execute(
            "SELECT bilaspur_relevance, count(*) AS n FROM articles "
            "GROUP BY 1")
    }
    languages = {
        row["language"] or "und": row["n"]
        for row in conn.execute(
            "SELECT language, count(*) AS n FROM articles GROUP BY 1")
    }
    per_source = _rows(conn.execute(
        "SELECT a.source_id, s.source_name, count(*) AS articles "
        "FROM articles a LEFT JOIN sources s ON s.source_id = a.source_id "
        "GROUP BY a.source_id, s.source_name ORDER BY articles DESC"))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "articles": scalar("SELECT count(*) FROM articles"),
        "sources_total": scalar("SELECT count(*) FROM sources"),
        "sources_enabled": scalar("SELECT count(*) FROM sources WHERE enabled=1"),
        "sources_by_state": sources_by_state,
        "relevance": relevance,
        "languages": languages,
        "sensitive": scalar("SELECT count(*) FROM articles WHERE sensitive=1"),
        "runs": scalar("SELECT count(*) FROM ingestion_runs"),
        "verifications": scalar("SELECT count(*) FROM source_verifications"),
        "articles_per_source": per_source,
        "last_run": _rows(conn.execute(
            "SELECT run_id, mode, started_at, finished_at, totals "
            "FROM ingestion_runs ORDER BY started_at DESC LIMIT 1")),
    }


def articles(conn: sqlite3.Connection, query: dict) -> dict:
    limit = min(int(query.get("limit", ["50"])[0] or 50), 500)
    offset = int(query.get("offset", ["0"])[0] or 0)
    where: list[str] = []
    args: list = []

    source = (query.get("source") or [""])[0].strip()
    if source:
        where.append("a.source_id = ?")
        args.append(source)
    relevance = (query.get("relevance") or [""])[0].strip()
    if relevance:
        where.append("a.bilaspur_relevance = ?")
        args.append(relevance)
    language = (query.get("language") or [""])[0].strip()
    if language:
        where.append("a.language = ?")
        args.append(language)
    sensitive = (query.get("sensitive") or [""])[0].strip()
    if sensitive in ("0", "1"):
        where.append("a.sensitive = ?")
        args.append(int(sensitive))
    search = (query.get("q") or [""])[0].strip()
    if search:
        where.append("(a.title LIKE ? OR a.content_text LIKE ?)")
        args.extend([f"%{search}%", f"%{search}%"])

    clause = f" WHERE {' AND '.join(where)}" if where else ""
    total = conn.execute(
        f"SELECT count(*) FROM articles a{clause}", args).fetchone()[0]

    rows = _rows(conn.execute(
        f"SELECT a.article_id, a.source_id, s.source_name, a.title, "
        f"a.canonical_url, a.language, a.bilaspur_relevance, "
        f"a.relevance_reason, a.matched_terms, a.sensitive, "
        f"a.published_date, a.published_at, a.published_time_precision, "
        f"a.published_at_original, a.extraction_method, a.parser_name, "
        f"a.record_status, a.source_verification_status, "
        f"a.first_seen_at, a.last_seen_at, a.run_id, "
        f"length(a.content_text) AS content_chars, "
        f"substr(a.content_text, 1, 320) AS excerpt "
        f"FROM articles a LEFT JOIN sources s ON s.source_id = a.source_id"
        f"{clause} ORDER BY a.article_id DESC LIMIT ? OFFSET ?",
        [*args, limit, offset]))
    return {"total": total, "limit": limit, "offset": offset,
            "articles": rows}


def article_detail(conn: sqlite3.Connection, article_id: int) -> dict | None:
    row = conn.execute(
        "SELECT a.*, s.source_name, s.url AS source_url "
        "FROM articles a LEFT JOIN sources s ON s.source_id = a.source_id "
        "WHERE a.article_id = ?", (article_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["revisions"] = _rows(conn.execute(
        "SELECT revision, captured_at, change_note, length(content_text) "
        "AS content_chars FROM article_revisions "
        "WHERE article_id = ? ORDER BY revision", (article_id,)))
    return data


def sources(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn.execute(
        "SELECT s.*, h.last_run_at, h.last_success_at, "
        "h.consecutive_failures, h.last_error_category, h.last_error_message, "
        "h.articles_total, "
        "(SELECT count(*) FROM articles a WHERE a.source_id = s.source_id) "
        "AS article_count "
        "FROM sources s LEFT JOIN source_health h "
        "ON h.source_id = s.source_id "
        "ORDER BY s.enabled DESC, s.priority, s.source_id"))
    for row in rows:
        row["collection_endpoints"] = _as_dict(row.get("collection_endpoints"))
    return rows


def runs(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = _rows(conn.execute(
        "SELECT * FROM ingestion_runs ORDER BY started_at DESC LIMIT ?",
        (limit,)))
    for row in rows:
        row["totals"] = _as_dict(row.get("totals"))
    return rows


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB
    server_version = "BilaspurDashboard/1.0"

    def log_message(self, *args):  # keep the console quiet
        pass

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False,
                          default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            self._route()
        except Exception as exc:  # noqa: BLE001 - never drop the connection
            # A handler crash must still answer the client, otherwise the
            # browser sees "connection closed" instead of a real error.
            try:
                self._send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:  # noqa: BLE001 - the socket may already be gone
                pass

    def _route(self) -> None:
        parsed = urlsplit(self.path)
        route = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if route == "/api/summary":
            with _connect(self.db_path) as conn:
                return self._send_json(summary(conn))
        if route == "/api/articles":
            with _connect(self.db_path) as conn:
                return self._send_json(articles(conn, query))
        if route == "/api/sources":
            with _connect(self.db_path) as conn:
                return self._send_json({"sources": sources(conn)})
        if route == "/api/runs":
            limit = min(int(query.get("limit", ["30"])[0] or 30), 200)
            with _connect(self.db_path) as conn:
                return self._send_json({"runs": runs(conn, limit)})
        if route.startswith("/api/article/"):
            try:
                article_id = int(route.rsplit("/", 1)[1])
            except ValueError:
                return self._send_json({"error": "bad article id"}, 400)
            with _connect(self.db_path) as conn:
                data = article_detail(conn, article_id)
            if data is None:
                return self._send_json({"error": "not found"}, 404)
            return self._send_json(data)

        if route in ("/", "/index.html"):
            return self._send_html()
        return self._send_json({"error": "not found"}, 404)

    def _send_html(self) -> None:
        page = ROOT / "scripts" / "dashboard.html"
        try:
            body = page.read_bytes()
        except OSError:
            return self._send_json(
                {"error": f"dashboard page missing at {page}"}, 500)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(port: int = 8766, db_path: Path | None = None):
    """Start the dashboard in a background thread.

    Returns `(base_url, server)`. Call `server.shutdown()` to stop it.
    The thread is a daemon, so the process exits cleanly either way.
    """
    resolved = Path(db_path) if db_path else DEFAULT_DB
    if not resolved.exists():
        raise FileNotFoundError(f"database not found: {resolved}")
    handler = type("BoundHandler", (DashboardHandler,), {"db_path": resolved})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", server


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilaspur scraper dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="path to the SQLite database (read-only)")
    args = parser.parse_args()

    base, server = start_server(args.port, Path(args.db))
    print(f"Dashboard running at {base}")
    print(f"Reading (read-only): {args.db}")
    print("Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
