"""
GET /api/cron — daily autorun (Vercel Cron, 09:00 UTC = 4 AM EST).

Vercel automatically sends `Authorization: Bearer $CRON_SECRET` to cron paths
when CRON_SECRET is set. We verify it so the endpoint can't be triggered by the
public. Returns a small JSON status; generating the report confirms the 7Shifts
pipeline is healthy each morning.
"""
import os
import sys
import json
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tardiness_core as core

CRON_SECRET = os.environ.get("CRON_SECRET", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        auth = self.headers.get("authorization", "")
        if CRON_SECRET and auth != f"Bearer {CRON_SECRET}":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": "unauthorized"}).encode())
            return
        try:
            _, stats = core.generate(6)
            body = {"ok": True, **stats}
            code = 200
        except Exception as e:  # noqa: BLE001
            body = {"ok": False, "error": str(e)}
            code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())
