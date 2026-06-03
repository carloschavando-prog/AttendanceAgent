"""GET /api/report?key=XXXX — serves the live rolling 6-month tardiness HTML."""
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Vercel does not add the api/ dir to sys.path — inject it so we can import the
# shared core module that lives alongside this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tardiness_core as core

REPORT_KEY = os.environ.get("REPORT_KEY", "4464")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        if REPORT_KEY and qs.get("key", [""])[0] != REPORT_KEY:
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Unauthorized - append ?key=YOUR_KEY")
            return
        try:
            months = int(qs.get("months", ["6"])[0])
        except ValueError:
            months = 6
        try:
            html_out, _ = core.generate(months)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "s-maxage=900, stale-while-revalidate=3600")
            self.end_headers()
            self.wfile.write(html_out.encode("utf-8"))
        except Exception as e:  # noqa: BLE001 — surface the error in the browser
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Report generation failed: {e}".encode("utf-8"))
