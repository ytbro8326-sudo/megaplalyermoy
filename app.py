"""
MegaPlayer Server
=================
Flask backend that:
  • Serves the player UI  (GET /)
  • Proxies HLS manifests + segments with spoofed Referer/Origin  (GET /proxy)
  • Persists per-host rules in profile.json  (GET/POST/DELETE /api/profiles)
  • Exposes last results.json if present  (GET /results)

Run locally:
    pip install flask requests gunicorn
    python app.py

Deploy to Render:
    Uses render.yaml — set SERVICE_URL env var after first deploy.

Deploy via GitHub Actions:
    Push to main → workflow calls Render deploy hook automatically.
"""

import json
import os
import re
import urllib.parse
from datetime import datetime, timezone

import requests
import urllib3
from flask import Flask, Response, jsonify, request, send_file

urllib3.disable_warnings()

# ── App setup ─────────────────────────────────────────────────────────────────
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PROFILES_PATH = os.path.join(BASE_DIR, "profile.json")
PORT          = int(os.environ.get("PORT", 6789))

app = Flask(__name__, static_folder=None)

# ── Upstream session ──────────────────────────────────────────────────────────
SESSION = requests.Session()
SESSION.verify = False

DEFAULT_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
    "Referer":         "https://megaplay.buzz/",
    "Origin":          "https://megaplay.buzz",
    "Accept":          "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Site":  "cross-site",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
}


# ── Helper: rewrite m3u8 so all internal URLs go through /proxy ───────────────
def rewrite_m3u8(content: str, original_url: str, referer: str, origin: str) -> str:
    base = original_url.rsplit("/", 1)[0] + "/"
    lines = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            # Rewrite URI="..." attributes inside tags (e.g. #EXT-X-KEY)
            def replace_uri(m):
                uri = m.group(1)
                abs_uri = urllib.parse.urljoin(base, uri)
                proxied = (
                    f"/proxy?url={urllib.parse.quote(abs_uri, safe='')}"
                    f"&ref={urllib.parse.quote(referer, safe='')}"
                    f"&origin={urllib.parse.quote(origin, safe='')}"
                )
                return f'URI="{proxied}"'
            line = re.sub(r'URI="([^"]+)"', replace_uri, line)
            lines.append(line)
        elif stripped:
            # Segment URL or sub-manifest
            abs_url = urllib.parse.urljoin(base, stripped)
            proxied = (
                f"/proxy?url={urllib.parse.quote(abs_url, safe='')}"
                f"&ref={urllib.parse.quote(referer, safe='')}"
                f"&origin={urllib.parse.quote(origin, safe='')}"
            )
            lines.append(proxied)
        else:
            lines.append(line)
    return "\n".join(lines)


# ── /proxy ────────────────────────────────────────────────────────────────────
@app.route("/proxy")
def proxy():
    url     = request.args.get("url", "").strip()
    referer = request.args.get("ref",    DEFAULT_HEADERS["Referer"])
    origin  = request.args.get("origin", DEFAULT_HEADERS["Origin"])

    if not url:
        return Response("Missing url param", 400)

    headers = {**DEFAULT_HEADERS, "Referer": referer, "Origin": origin}

    try:
        upstream = SESSION.get(url, headers=headers, timeout=20, stream=True)
    except Exception as exc:
        return Response(f"Proxy error: {exc}", 502)

    content_type = upstream.headers.get("Content-Type", "application/octet-stream")
    is_m3u8 = (
        "mpegurl" in content_type.lower()
        or url.split("?")[0].lower().endswith(".m3u8")
    )

    if is_m3u8:
        body = upstream.content.decode("utf-8", errors="ignore")
        rewritten = rewrite_m3u8(body, url, referer, origin)
        return Response(
            rewritten,
            status=upstream.status_code,
            content_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    # Segments / subtitles — stream through
    def generate():
        for chunk in upstream.iter_content(chunk_size=65536):
            if chunk:
                yield chunk

    resp = Response(generate(), status=upstream.status_code, content_type=content_type)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


# ── / — serve player ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_file(os.path.join(BASE_DIR, "megaplayer.html"))


# ── /results — serve last results.json if present ─────────────────────────────
@app.route("/results")
def results():
    path = os.path.join(BASE_DIR, "results.json")
    if os.path.exists(path):
        return send_file(path, mimetype="application/json")
    return jsonify({}), 404


# ── /health — uptime ping for Render ─────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ── Profile helpers ───────────────────────────────────────────────────────────
def _read_profiles() -> dict:
    if os.path.exists(PROFILES_PATH):
        try:
            with open(PROFILES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _write_profiles(data: dict) -> None:
    with open(PROFILES_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── /api/profiles ─────────────────────────────────────────────────────────────
@app.route("/api/profiles", methods=["GET"])
def profiles_get():
    return jsonify(_read_profiles())


@app.route("/api/profiles", methods=["POST"])
def profiles_post():
    body = request.get_json(force=True, silent=True) or {}
    host = body.get("host", "").strip()
    if not host:
        return jsonify({"error": "Missing host"}), 400
    data = _read_profiles()
    data[host] = {k: v for k, v in body.items() if k != "host"}
    data[host].setdefault(
        "saved", datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    _write_profiles(data)
    return jsonify({"ok": True, "host": host})


@app.route("/api/profiles/<path:host>", methods=["DELETE"])
def profiles_delete(host):
    data = _read_profiles()
    removed = host in data
    data.pop(host, None)
    _write_profiles(data)
    return jsonify({"ok": True, "removed": removed})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import threading, webbrowser, time

    def _open():
        time.sleep(1.2)
        webbrowser.open(f"http://localhost:{PORT}")

    threading.Thread(target=_open, daemon=True).start()
    print(f"\n  ╔══════════════════════════════════════╗")
    print(f"  ║   MegaPlayer  →  http://localhost:{PORT}  ║")
    print(f"  ╚══════════════════════════════════════╝")
    print(f"  Press Ctrl+C to stop.\n")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
