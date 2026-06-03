"""
AttendanceAgent — core tardiness logic for On Par Bar (7Shifts).
=================================================================
Stdlib-only (urllib) so it runs on Vercel with no dependencies.

Tardiness = actual punch-in (clocked_in) - scheduled punch-in (linked shift.start),
using ONLY the earliest punch per (user, shift) so break-returns/split segments
don't read as multi-hour lateness. Manager (salaried) and Cleaner are excluded.

Produces a rolling N-month HTML report with a by-employee leaderboard plus
every late arrival.
"""

import os
import json
import html
import calendar
import urllib.parse
import urllib.request
from collections import defaultdict, Counter
from datetime import date, datetime, timezone, timedelta

# ---- Config (env-driven; safe defaults) ------------------------------------
TOKEN = os.environ.get("SHIFTS_API_TOKEN", "").strip()
CO    = os.environ.get("SHIFTS_COMPANY_ID", "286488").strip()
LOC   = os.environ.get("SHIFTS_LOCATION_ID", "354876").strip()
BASE  = os.environ.get("SHIFTS_BASE_URL", "https://api.7shifts.com").strip()

GRACE_MIN     = 0
EXCLUDE_ROLES = {1760491}    # Manager — salaried
EXCLUDE_DEPTS = {545687}     # Cleaner — external vendor

ROLE_CATEGORY = {
    1760490: "foh", 1760495: "kit", 1760496: "kit", 1761419: "foh",
    1780081: "kit", 2045831: "foh", 2103857: "kit", 2215217: "foh",
    2332059: "kit", 2686259: "kit", 2754779: "kit",
}


# ---- HTTP -------------------------------------------------------------------
def _get(path, params):
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def get_paginated(path, params):
    """Cursor-paginated GET. Returns list of data objects."""
    out, cursor = [], None
    while True:
        p = dict(params)
        if cursor:
            p["cursor"] = cursor
        body = _get(path, p)
        out.extend(body.get("data", []))
        cursor = (body.get("meta", {}) or {}).get("cursor", {}).get("next")
        if not cursor:
            return out


# ---- Date helpers -----------------------------------------------------------
def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def months_ago(d, n):
    m, y = d.month - n, d.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def rolling_window(months=6):
    to = date.today()
    return months_ago(to, months).isoformat(), to.isoformat()


# ---- Core computation -------------------------------------------------------
def build(frm, to):
    punches = get_paginated(
        f"/v2/company/{CO}/time_punches",
        {"clocked_in[gte]": f"{frm}T00:00:00", "clocked_in[lte]": f"{to}T23:59:59",
         "location_id": LOC, "limit": 200},
    )
    shifts = get_paginated(
        f"/v2/company/{CO}/shifts",
        {"start[gte]": frm, "start[lte]": to, "location_id": LOC, "limit": 200},
    )
    sched = {s["id"]: parse_ts(s.get("start")) for s in shifts}
    users = get_paginated(f"/v2/company/{CO}/users", {"limit": 200})
    names = {u["id"]: f'{u.get("first_name","").strip()} {u.get("last_name","").strip()}'.strip()
             for u in users}

    # earliest punch per (user, shift) = arrival
    arrival = {}
    for p in punches:
        if p.get("deleted") or not p.get("shift_id"):
            continue
        ts = parse_ts(p.get("clocked_in"))
        if ts is None:
            continue
        key = (p.get("user_id"), p.get("shift_id"))
        if key not in arrival or ts < parse_ts(arrival[key].get("clocked_in")):
            arrival[key] = p

    rows = []
    for p in arrival.values():
        if p.get("role_id") in EXCLUDE_ROLES or p.get("department_id") in EXCLUDE_DEPTS:
            continue
        actual = parse_ts(p.get("clocked_in"))
        scheduled = sched.get(p.get("shift_id"))
        if actual is None or scheduled is None:
            continue
        late_min = round((actual - scheduled).total_seconds() / 60)
        if late_min <= GRACE_MIN:
            continue
        rows.append({
            "name": names.get(p.get("user_id"), f'User {p.get("user_id")}'),
            "cat": ROLE_CATEGORY.get(p.get("role_id"), "?").upper(),
            "date": scheduled.strftime("%a %b %-d, %Y"),
            "sched": scheduled.strftime("%-I:%M %p"),
            "actual": actual.strftime("%-I:%M %p"),
            "late": late_min,
            "_sort": actual,
        })
    rows.sort(key=lambda r: (-r["late"], r["_sort"]))

    # Per-employee department, inferred from the roles they're scheduled in (used
    # for the Dept column of staff who have call-offs but no late arrivals).
    uid_roles = defaultdict(Counter)
    for s in shifts:
        if s.get("role_id") in ROLE_CATEGORY:
            uid_roles[s.get("user_id")][ROLE_CATEGORY[s["role_id"]]] += 1
    name_cat = {names.get(uid, f"User {uid}"): c.most_common(1)[0][0].upper()
                for uid, c in uid_roles.items()}

    # Call-offs come straight from the official 7Shifts Attendance Report so the
    # numbers match what staff see in the UI (raw shift attendance_status counts
    # higher — the report excludes drafts/unworked shifts via internal logic).
    attendance = {}
    try:
        rep = _get(f"/v2/company/{CO}/reports/attendance",
                   {"location_id": LOC, "start_date": frm, "end_date": to}).get("data", [])
        for r in rep:
            attendance[r["name"]] = {k: r.get(k, 0) or 0
                                     for k in ("sick", "no_show", "called_off", "called_in")}
    except Exception:
        attendance = {}

    return rows, len(punches), attendance, name_cat


def summarize(rows, attendance, name_cat):
    """One row per employee who has a late arrival OR any call-off."""
    agg = {}

    def slot(name):
        if name not in agg:
            agg[name] = {"name": name, "cat": name_cat.get(name, "?"),
                         "count": 0, "total": 0, "worst": 0,
                         "sick": 0, "no_show": 0, "called_off": 0, "called_in": 0}
        return agg[name]

    for r in rows:
        a = slot(r["name"])
        a["cat"] = r["cat"]              # role from the actual late punch is most precise
        a["count"] += 1
        a["total"] += r["late"]
        a["worst"] = max(a["worst"], r["late"])

    for name, co in attendance.items():
        if any(co.values()):
            a = slot(name)
            for k in ("sick", "no_show", "called_off", "called_in"):
                a[k] += co[k]

    out = []
    for a in agg.values():
        a["avg"] = round(a["total"] / a["count"]) if a["count"] else 0
        a["calloffs"] = a["sick"] + a["no_show"] + a["called_off"] + a["called_in"]
        out.append(a)
    # Keep the approved late-arrival ranking on top; call-off-only staff follow,
    # ordered by how many call-offs they have.
    out.sort(key=lambda s: (-s["count"], -s["calloffs"], -s["total"]))
    return out


def render_html(rows, frm, to, total_punches, months, attendance, name_cat):
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = summarize(rows, attendance, name_cat)

    def co_cell(v):  # call-off cell: dim zeros, red the non-zero counts
        return f'<td class="num co">{v}</td>' if v else '<td class="num zero">0</td>'

    def late_cells(s):
        if not s["count"]:
            return '<td class="num zero">0</td><td class="num zero">—</td><td class="num zero">—</td>'
        return (f'<td class="num">{s["count"]}</td>'
                f'<td class="num">{s["avg"]} min</td>'
                f'<td class="num late {"bad" if s["worst"]>=15 else "warn"}">{s["worst"]} min</td>')

    sum_trs = "\n".join(
        f'<tr><td>{html.escape(s["name"])}</td>'
        f'<td><span class="pill {s["cat"].lower()}">{s["cat"]}</span></td>'
        + late_cells(s)
        + co_cell(s["sick"]) + co_cell(s["no_show"])
        + co_cell(s["called_off"]) + co_cell(s["called_in"])
        + '</tr>'
        for s in summary
    ) or '<tr><td colspan="9" class="none">No attendance issues in this window. 🎉</td></tr>'

    tot_ns = sum(s["no_show"] for s in summary)
    tot_sick = sum(s["sick"] for s in summary)
    tot_co = sum(s["called_off"] + s["called_in"] for s in summary)

    # Detail table grouped by staff member (alphabetical), chronological within.
    detail = sorted(rows, key=lambda r: (r["name"].lower(), r["_sort"]))
    trs = "\n".join(
        f'<tr class="{ "bad" if r["late"]>=15 else "warn" }">'
        f'<td>{html.escape(r["name"])}</td>'
        f'<td><span class="pill {r["cat"].lower()}">{r["cat"]}</span></td>'
        f'<td>{r["date"]}</td><td>{r["sched"]}</td><td>{r["actual"]}</td>'
        f'<td class="late num">{r["late"]} min</td></tr>'
        for r in detail
    ) or '<tr><td colspan="6" class="none">No late arrivals in this window. 🎉</td></tr>'

    worst = max((r["late"] for r in rows), default=0)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tardiness Report — On Par Bar</title>
<style>
 body{{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#0f1115;color:#e8eaed}}
 .wrap{{max-width:920px;margin:0 auto;padding:32px 20px}}
 h1{{margin:0 0 4px;font-size:24px}}
 h2{{margin:32px 0 12px;font-size:17px;color:#cdd2d8}}
 .sub{{color:#9aa0a6;margin-bottom:24px;font-size:14px}}
 .cards{{display:flex;gap:14px;margin-bottom:8px;flex-wrap:wrap}}
 .card{{background:#1b1e24;border:1px solid #2a2e36;border-radius:12px;padding:16px 20px;flex:1;min-width:130px}}
 .card .n{{font-size:28px;font-weight:700}}
 .card .l{{color:#9aa0a6;font-size:13px;margin-top:2px}}
 table{{width:100%;border-collapse:collapse;background:#1b1e24;border-radius:12px;overflow:hidden}}
 th,td{{text-align:left;padding:10px 14px;border-bottom:1px solid #2a2e36}}
 th{{background:#22262e;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#9aa0a6}}
 tr:last-child td{{border-bottom:none}}
 .num{{text-align:right}}
 .late{{font-weight:700}}
 .co{{font-weight:700;color:#ff6b6b}} .zero{{color:#5b616b}}
 .bad.late,tr.bad .late{{color:#ff6b6b}} .warn.late,tr.warn .late{{color:#ffb454}}
 .none{{text-align:center;color:#9aa0a6;padding:28px}}
 .pill{{font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px}}
 .pill.foh{{background:#1e3a5f;color:#7db5ff}} .pill.kit{{background:#5f3a1e;color:#ffb87d}}
 .pill.\\?{{background:#333;color:#aaa}}
 .foot{{color:#6b7280;font-size:12px;margin-top:18px}}
</style></head><body><div class="wrap">
<h1>Tardiness Report — On Par Bar</h1>
<div class="sub">Rolling {months}-month window: <b>{frm}</b> → <b>{to}</b> · generated {generated}</div>
<div class="cards">
 <div class="card"><div class="n">{len(rows)}</div><div class="l">Late arrivals</div></div>
 <div class="card"><div class="n">{tot_ns}</div><div class="l">No-shows</div></div>
 <div class="card"><div class="n">{tot_sick}</div><div class="l">Sick days</div></div>
 <div class="card"><div class="n">{tot_co}</div><div class="l">Called off / in</div></div>
 <div class="card"><div class="n">{worst}</div><div class="l">Worst (min late)</div></div>
 <div class="card"><div class="n">{total_punches}</div><div class="l">Punches reviewed</div></div>
</div>

<h2>By employee</h2>
<table>
 <tr><th>Employee</th><th>Dept</th><th class="num">Late arrivals</th><th class="num">Avg late</th><th class="num">Worst</th><th class="num">Sick</th><th class="num">No-show</th><th class="num">Called off</th><th class="num">Called in</th></tr>
 {sum_trs}
</table>

<h2>Every late arrival</h2>
<table>
 <tr><th>Employee</th><th>Dept</th><th>Date (scheduled)</th><th>Sched. in</th><th>Actual in</th><th class="num">Late by</th></tr>
 {trs}
</table>
<div class="foot">Late arrivals: actual − scheduled punch-in (earliest punch per shift), Manager &amp; Cleaner excluded.
 Sick / No-show / Called off / Called in come from the 7Shifts Attendance Report (the report's Late column is omitted — covered above). ≥15 min &amp; any call-off shown in red.</div>
</div></body></html>"""


def generate(months=6):
    frm, to = rolling_window(months)
    rows, total, attendance, name_cat = build(frm, to)
    stats = {"from": frm, "to": to, "late_arrivals": len(rows), "punches_reviewed": total,
             "no_shows": sum(c["no_show"] for c in attendance.values()),
             "sick": sum(c["sick"] for c in attendance.values()),
             "called_off": sum(c["called_off"] for c in attendance.values()),
             "called_in": sum(c["called_in"] for c in attendance.values())}
    return render_html(rows, frm, to, total, months, attendance, name_cat), stats
