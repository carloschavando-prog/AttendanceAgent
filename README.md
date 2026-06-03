# AttendanceAgent

Staff tardiness monitor for **On Par Bar** (On Par Entertainment), built on the
7Shifts API and hosted on Vercel.

It compares each employee's **actual punch-in** against their **scheduled
punch-in** and reports how late they were, as a rolling 6-month HTML report with
a by-employee leaderboard plus every individual late arrival.

## Live

- **Report:** `https://<deployment>.vercel.app/api/report?key=REPORT_KEY`
  (or just `https://<deployment>.vercel.app/?key=REPORT_KEY`)
- **Daily autorun:** Vercel Cron hits `/api/cron` every day at **09:00 UTC = 4:00 AM EST**.

## How tardiness is measured

- **Actual** = the time punch's `clocked_in`.
- **Scheduled** = the linked shift's `start` (joined via `punch.shift_id`).
- **Late (min)** = `clocked_in − shift.start`.
- Only the **earliest punch per (employee, shift)** counts as the arrival, so
  break-returns / split-shift segments don't read as multi-hour "lateness."
- **Excluded:** Manager role (salaried) and the Cleaner department (external vendor).

## Layout

| Path | Purpose |
|------|---------|
| `api/report.py` | Serves the live HTML report (key-gated). |
| `api/cron.py` | Daily autorun endpoint (CRON_SECRET-protected). |
| `api/tardiness_core.py` | Shared fetch + compute + render logic (stdlib only). |
| `vercel.json` | Cron schedule + function config. |

## Environment variables (set in Vercel)

| Var | Purpose |
|-----|---------|
| `SHIFTS_API_TOKEN` | 7Shifts Bearer token (raw hex — do **not** decode). |
| `SHIFTS_COMPANY_ID` | `286488` |
| `SHIFTS_LOCATION_ID` | `354876` |
| `SHIFTS_BASE_URL` | `https://api.7shifts.com` |
| `REPORT_KEY` | Gate for the public report URL (`?key=...`). |
| `CRON_SECRET` | Vercel sends this as `Authorization: Bearer` to the cron path. |

## Local run

```bash
cp .env.template .env   # fill in the token
python3 -c "import os,api.tardiness_core as c; open('out.html','w').write(c.generate(6)[0])"
```
