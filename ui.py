#!/usr/bin/env python3
"""Local Pendant browser: http://localhost:8766

Reads the transcripts pendant_sync.py appends to ~/notes/raw/pendant/,
plays the matching audio, shows sync status, and can trigger a sync.
Keep it running with a launchd agent (or just run it); stdlib only.
"""
import json
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HOME = Path(__file__).resolve().parent
WIKI_RAW = Path.home() / "notes/raw/pendant"
PORT = 8766
ENTRY = re.compile(r"^## (\d\d:\d\d) \((\d+)s\)\n\n(.*?)\n\n<!-- audio: (.*?) -->", re.M | re.S)


def day_entries(day: str):
    f = WIKI_RAW / f"{day}.md"
    if not f.exists():
        return []
    return [{"time": t, "seconds": int(s), "text": txt.strip(), "audio": a}
            for t, s, txt, a in ENTRY.findall(f.read_text())]


def days():
    out = []
    for f in sorted(WIKI_RAW.glob("*.md"), reverse=True):
        es = day_entries(f.stem)
        out.append({"day": f.stem, "count": len(es), "seconds": sum(e["seconds"] for e in es)})
    return out


def search(q: str):
    q = q.lower()
    hits = []
    for d in days():
        for e in day_entries(d["day"]):
            if q in e["text"].lower():
                hits.append({**e, "day": d["day"]})
    return hits[:200]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p = unquote(u.path)
        if p == "/":
            return self.send((HOME / "ui.html").read_bytes(), "text/html; charset=utf-8")
        if p == "/api/status":
            try:
                st = json.loads((HOME / "status.json").read_text())
            except Exception:
                st = {}
            st["syncing"] = (HOME / ".sync.running").exists()
            return self.send(st)
        if p == "/api/days":
            return self.send(days())
        if p.startswith("/api/day/"):
            return self.send(day_entries(p.rsplit("/", 1)[1]))
        if p == "/api/search":
            q = dict(x.split("=", 1) for x in u.query.split("&") if "=" in x).get("q", "")
            return self.send(search(unquote(q.replace("+", " "))))
        if p.startswith("/audio/"):
            f = (HOME / p[len("/audio/"):]).resolve()
            if HOME in f.parents and f.exists() and f.suffix == ".opus":
                return self.send(f.read_bytes(), "audio/ogg")
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        if self.path == "/api/sync":
            marker = HOME / ".sync.running"
            if not marker.exists():
                marker.touch()
                # Same script launchd runs; its file lock prevents overlapping drains.
                subprocess.Popen(["/bin/sh", "-c", f'"{sys.executable}" "{HOME}/pendant_sync.py"; rm -f "{marker}"'],
                                 cwd=HOME, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return self.send({"started": True})
        self.send({"error": "not found"}, code=404)


if __name__ == "__main__":
    print(f"Pendant UI on http://localhost:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
