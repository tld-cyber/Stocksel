#!/usr/bin/env python3
"""Build a SELF-CONTAINED snapshot of the screener — one HTML file, no backend, no key, no network.

The desktop app fetches from its local Flask backend, which is what lets prices update through the
day. A shared file cannot do that: opened from disk or from a static host there is no backend to
call, and Yahoo cannot be reached from a browser at all (it sends no CORS headers). So this bakes a
scan into the page.

It reuses frontend/index.html verbatim — same CSS, same table, same rendering — and only replaces
the bootstrap at the bottom: ROWS arrives pre-loaded, and the refresh control is REMOVED rather than
left there to fail against a backend that is not present.

Three modes, split because the two halves of the data age at completely different rates:

  --scan     full scan -> data/floors.json. Slow (~2 min). Weekly data; once a day is plenty.
  --prices   floors.json + fresh prices -> the HTML. Fast (~1 min). This is the one on a timer.
  (neither)  do both in one go — what you want when running it by hand.

That split is what makes a 15-minute refresh schedule affordable: re-scanning 6,200 names every
quarter hour to update a price would be ~2 minutes of compute each time, against ~1 for prices alone,
and would re-derive floors that provably cannot have changed.
"""
import json
import os
import sys
from datetime import datetime

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import screener

HERE = os.path.dirname(os.path.abspath(__file__))
FLOORS = os.path.join(HERE, "data", "floors.json")
OUT = os.path.join(HERE, "site", "index.html")
ET = pytz.timezone("America/New_York")

BOOTSTRAP = """$("status").hidden = false;
load();
// Prices only — the floors are weekly and recomputing them intraday would be wrong, not just slow.
timer = setInterval(refreshPrices, 5 * 60 * 1000);"""


def do_scan():
    print("scanning (no API key — Yahoo + Nasdaq's public symbol file)…", flush=True)
    rows, meta = screener.scan(lambda s: print(f"  {s}", flush=True) if s else None)
    os.makedirs(os.path.dirname(FLOORS), exist_ok=True)
    with open(FLOORS, "w") as f:
        json.dump({"rows": rows, "meta": meta}, f, separators=(",", ":"))
    print(f"wrote {FLOORS} — {meta['scanned']:,} names, floors as of {meta['floor_asof']}")
    return rows, meta


def load_floors():
    if not os.path.exists(FLOORS):
        print("no floors.json yet — running a full scan first")
        return do_scan()
    with open(FLOORS) as f:
        d = json.load(f)
    return d["rows"], d["meta"]


def do_prices(rows, meta):
    # Price both ends only: everything at or through the floor, plus the furthest-above view. The
    # middle of the distribution is never rendered, so pricing it would only invite throttling.
    print("fetching current prices…", flush=True)
    ends = [r["sym"] for r in rows[:1500]] + [r["sym"] for r in rows[-500:]]
    rows = screener.apply_prices(rows, screener.refresh(ends))
    # Recount AFTER pricing: floors.json carries D% seeded from weekly closes, which can never be
    # below a floor derived from those same bars. Only a live price can be, and that count is the
    # headline. Reported 0 once while the table plainly showed -51%.
    meta = dict(meta)
    meta["below"] = sum(1 for r in rows if r["d"] < 0)
    meta["generated"] = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    return rows, meta


def build(rows, meta):
    # Ship the two ends as SEPARATE arrays. Concatenating them into one list was a real bug: the
    # page renders a single array in order, so after the volume filter shrank the head to 223 rows
    # the tail began at row 224 and D% leapt 4.4% -> 206.2% mid-table. Nothing marked the boundary,
    # so it read as corrupt data rather than as the omission it was — every name between those two
    # values simply is not in the file. Separate pools make the seam impossible: each view draws
    # only from the end it is actually about.
    lo, hi = rows[:900], rows[-300:]
    meta = dict(meta, shown=len(lo) + len(hi))
    html = open(os.path.join(HERE, "frontend", "index.html"), encoding="utf-8").read()
    assert BOOTSTRAP in html, "frontend bootstrap block moved — update BOOTSTRAP to match"
    html = html.replace(BOOTSTRAP, (
        "// Static snapshot: data is baked in, there is no backend to call.\n"
        f"ROWS = {json.dumps(lo, separators=(',', ':'))};\n"
        f"ROWS_HI = {json.dumps(hi, separators=(',', ':'))};\n"
        f"setMeta({json.dumps(meta)}, {json.dumps(meta['generated'])});\n"
        "$('status').hidden = true;\n"
        "$('refresh').remove();   // no backend behind it — a dead control is worse than none\n"
        "render();"))
    html = html.replace("<title>Stocksel — 52-week floor</title>",
                        "<title>Stocksel — 52-week floor (snapshot)</title>")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nwrote {OUT}  ({os.path.getsize(OUT)/1024:,.0f} KB)")
    print(f"  {meta['scanned']:,} scanned · {meta['below']:,} below floor · {meta['shown']:,} rows shipped")
    print(f"  floors as of {meta['floor_asof']} · generated {meta['generated']}")


def main():
    args = sys.argv[1:]
    if "--scan" in args:
        do_scan()
        return
    rows, meta = load_floors() if "--prices" in args else do_scan()
    build(*do_prices(rows, meta))


if __name__ == "__main__":
    main()
