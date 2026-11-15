"""A localhost site whose news only exists after JavaScript runs.

This is the proof case for the browser adapter: the HTTP path sees an
empty shell (no article links at all), while a rendered Chromium fetch
sees the injected listing and the injected article bodies.

Run it standalone:

    python scripts/js_demo_site.py --port 8765

Then point the scraper at it:

    python -m bilaspur_scraper.main verify-source js-demo-site --browser --by "demo"
    python -m bilaspur_scraper.main run js-demo-site

The module is also imported by the test suite (`tests/unit/test_browser_fetching.py`).
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROBOTS = b"User-agent: *\nAllow: /\n"

# Three Bilaspur/Chhattisgarh stories. The content is realistic enough to
# pass the extraction gates (title >= 8 chars, body >= 300 chars).
STORIES = [
    {
        "slug": "bilaspur-flyover-review",
        "title": "बिलासपुर के फ्लाईओवर की समीक्षा, CS ने लागत के आंकलन का दिया निर्देश",
        "date": "2026-11-12",
        "body": (
            "बिलासपुर छत्तीसगढ़ के फ्लाईओवर और रेलवे ओवरब्रिज की समीक्षा के दौरान "
            "मुख्य सचिव ने लागत के आंकलन का निर्देश दिया। अधिकारियों ने बताया "
            "कि परियोजना पर दी गई राशि का सदुपयोग सुनिश्चित करने के लिए तीसरी "
            "पक्षकारी जांच की जाएगी। नगर निगम अधिकारियों के अनुसार आने वाले "
            "महीनों में सड़क, पुलिस लाइनिंग और बस स्टैंड के लिए भी अलग से "
            "योजनाबद्ध कार्रवाई होगी।"
        ),
    },
    {
        "slug": "kota-adivasi-land-registrations",
        "title": "कोटा में आदिवासी जमीन की खरीद-बिक्री का आरोप, रजिस्ट्री पर जांच शुरू",
        "date": "2026-11-11",
        "body": (
            "बिलासपुर जिले के कोटा क्षेत्र में आदिवासी जमीन की खरीद-बिक्री के "
            "मामले में रजिस्ट्री कार्यालय ने जांच शुरू की है। ग्राम सभा में किए "
            "गए प्रश्नों के बाद रजिस्ट्रार ने संबंधित दस्तावेजों की जांच के "
            "लिए टीम भेजी है। पड़ोसी गांवों के किसानों ने भी सरकार से इस मामले "
            "में स्वतंत्र जांच की मांग की है और कहा है कि किसी भी किसान को "
            "बिना उचित प्रक्रिया के जमीन से विलोपित नहीं किया जाना चाहिए।"
        ),
    },
    {
        "slug": "secl-bilaspur-recruitment",
        "title": "SECL बिलासपur में भर्ती: मल्हार खदान के लिए नए पदों पर आवेदन शुरू",
        "date": "2026-11-10",
        "body": (
            "साउथ ईस्टर्न कोलफील्ड्स लिमिटेड ने बिलासपur स्थित मल्हार "
            "खदान क्षेत्र में विभिन्न पदों के लिए आवेदन आमंत्रित किए हैं। "
            "कंपनी के अनुसार चयन प्रक्रिया पूरी होने के बाद उम्मीदवारों को "
            "कंपनी के सुरक्षा नियमों के अनुसार प्रशिक्षण दिया जाएगा। "
            "स्थानीय रोजगार और प्रशिक्षण पर जोर देते हुए कंपनी ने कहा है "
            "कि चयन प्रक्रिया में स्थानीय योग्यता को प्राथमिकता दी जाएगी।"
        ),
    },
]

LISTING_HTML = """<!DOCTYPE html>
<html lang="hi">
<head>
  <meta charset="utf-8">
  <title>बिलासपुर जिला समाचार (JS demo)</title>
  <meta name="generator" content="js-demo-site">
</head>
<body>
  <header><h1>बिलासपुर जिला समाचार</h1></header>
  <main id="listing">
    <!-- The list is injected by JavaScript: a plain HTTP fetch sees no
         article links at all, so only a rendered browser can find them. -->
    <p class="loading">समाचार लोड हो रहे हैं…</p>
  </main>
  <script>
    fetch('/api/listing.json')
      .then(function (r) { return r.json(); })
      .then(function (items) {
        var main = document.getElementById('listing');
        main.innerHTML = '';
        items.forEach(function (item) {
          var a = document.createElement('a');
          a.className = 'js-story';
          a.href = '/news/' + item.slug;
          a.textContent = item.title;
          var li = document.createElement('li');
          li.appendChild(a);
          main.appendChild(li);
        });
      })
      .catch(function (err) {
        document.getElementById('listing').textContent = 'load failed: ' + err;
      });
  </script>
</body>
</html>"""

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="hi">
<head>
  <meta charset="utf-8">
  <title>समाचार</title>
</head>
<body>
  <main id="article">
    <!-- Article title and body are injected by JavaScript. -->
    <p class="loading">लोड हो रहा है…</p>
  </main>
  <script>
    var slug = window.location.pathname.split('/').pop();
    fetch('/api/article/' + slug + '.json')
      .then(function (r) { return r.json(); })
      .then(function (item) {
        document.title = item.title;
        var main = document.getElementById('article');
        var h1 = document.createElement('h1');
        h1.id = 'story-title';
        h1.textContent = item.title;
        var time = document.createElement('time');
        time.id = 'story-date';
        time.setAttribute('datetime', item.date);
        time.textContent = item.date;
        var art = document.createElement('article');
        art.id = 'story-body';
        var p = document.createElement('p');
        p.textContent = item.body;
        art.appendChild(p);
        main.innerHTML = '';
        main.appendChild(h1);
        main.appendChild(time);
        main.appendChild(art);
      });
  </script>
</body>
</html>"""


def _story_json(story: dict) -> bytes:
    return json.dumps(story, ensure_ascii=False).encode("utf-8")


class JsDemoHandler(BaseHTTPRequestHandler):
    """Serves the JS-rendered demo site."""

    def log_message(self, *args):  # silence
        pass

    def _send(self, body: bytes, content_type="text/html; charset=utf-8",
              status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/robots.txt":
            self._send(ROBOTS, "text/plain")
        elif path == "/":
            self._send(LISTING_HTML.encode("utf-8"))
        elif path == "/api/listing.json":
            payload = [{"slug": s["slug"], "title": s["title"],
                        "date": s["date"]} for s in STORIES]
            self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json")
        elif path.startswith("/api/article/"):
            slug = path[len("/api/article/"):].removesuffix(".json")
            story = next((s for s in STORIES if s["slug"] == slug), None)
            if story is None:
                self._send(b"not found", "text/plain", 404)
            else:
                self._send(_story_json(story), "application/json")
        elif path.startswith("/news/"):
            self._send(ARTICLE_HTML.encode("utf-8"))
        else:
            self._send(b"not found", "text/plain", 404)


def start_server(port: int = 0):
    """Start the demo site in a background thread.

    Returns `(base_url, server)`; call `server.shutdown()` to stop it.
    """
    server = ThreadingHTTPServer(("127.0.0.1", port), JsDemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_port}", server


def main() -> int:
    parser = argparse.ArgumentParser(description="JS-rendered demo news site")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    base, server = start_server(args.port)
    print(f"JS demo site running at {base}")
    print("HTTP-only fetch sees an empty listing; the browser adapter sees it.")
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
