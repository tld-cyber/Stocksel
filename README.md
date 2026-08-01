# STOCKSEL v5 — 52-week floor, keyless (Yahoo)

**Role:** the same positional screen as **`../V4`** — *where does this stock sit against its own 52-week floor?* — rebuilt so that **nobody needs an API key**. That is the entire reason v5 exists: Alpaca requires a key per person, and handing the tool to someone else meant either sharing credentials or asking them to sign up. Yahoo needs neither.

Two ways to run it: a **desktop app** (native window, live prices) and a **published web page** at **https://tld-cyber.github.io/Stocksel/** that anyone can open with no install, no key, no account.

This directory is its own git repo — `github.com/tld-cyber/Stocksel`, public — and the only part of this project on the web.

```bash
./run.sh                                  # desktop app (first run makes a venv)
python3 publish_static.py                 # full scan + prices -> docs/index.html
python3 publish_static.py --prices        # prices only, ~1 min (reads data/floors.json)
python3 publish_static.py --scan          # floors only -> data/floors.json
python3 -m backend.server                 # backend alone, no window
```

**Why a local backend and not just a web page:** Yahoo sends no CORS headers, so a browser cannot read its response at all — it fetches, then throws the answer away. Python can. That one fact drove the whole shape. Alpaca *does* publish CORS headers and could be called from a page directly, but only with a key embedded in it.

---

## Inputs

| Source | Used for | Key? |
|---|---|---|
| **nasdaqtrader.com symbol file** | the universe — ~6,200 listed commons | no |
| **Yahoo weekly bars** via `yfinance`, `auto_adjust=False` | 52 weeks of OHLCV → floor, its date, how long it held, touch counts, ceiling, typical volume | no |
| **Yahoo 1-minute bars** via `yfinance` | the current price (`Xnow`), regular session only | no |

**No `.env`, no credentials, nothing to configure.** `auto_adjust=False` is deliberate: it leaves prices split-adjusted but *not* dividend-adjusted, so a floor is a price the stock actually traded at. It reproduces V4's Alpaca figures — AAPL's floor comes out at 201.50 on both, and touch counts match name for name.

**Why the universe comes from Nasdaq rather than a broker:** that file carries an explicit **ETF flag**, so Treasury and bond funds are excluded by classification. V4 had to infer "this is a fund" from the fact that it barely moves.

## Drivers

Same columns as V4 — `Xlow`, `Xnow`, `Xhigh`, `D%`, `When`, `tch`, and the `Xlow⁻⁵ / Xlow⁻¹⁰ / Xlow⁻²⁰` bands — with two additions:

- **A 52-week trace** per row: the year in weekly closes, with a red dot on the week that set the low. Hovering names that week. Deliberately axis-free — it shows the shape of the year, not values. Note the line is *closes* while the low is an *intraday* low, so the dot marks the week rather than the exact point.
- **Touches follow the selected band.** Counts are computed at 5, 10 and 25%, and the `tch` column reads whichever `within` is set. Describing a touch as *"came back inside the selected range"* is therefore literally true, rather than a fixed-5% number wearing a label implying otherwise.

**The current week is excluded from `Xlow`**, same as V4: over a window including today the low is self-referential, so D% could never go negative and *"what broke down"* would return nothing, forever, while looking like it worked.

**`When` and `tch` come apart badly.** A floor 9 months old touched **once** was never a real level — a single spike-down that happens to be the year's lowest point. One touched 14 times is a price people keep buying at. Read `tch` first.

## Outputs

- **`docs/index.html`** — the published page. Self-contained, zero external requests, ~700 KB. **Pages serves `docs/` straight from the branch, so committing it IS publishing it** — no artifact upload, no deploy step.
- **`data/floors.json`** — the weekly half of the data, committed on purpose so the fast price job can skip the scan.
- **Desktop app** — `app.py` opens a pywebview window over a local Flask backend, the same pattern as the World News Globe project. Refreshes prices on its own timer, no schedule involved.

**The two halves have different clocks.** `scan()` is slow (~2–3 min) and produces weekly data that cannot change during a session. `refresh()` takes seconds and updates only `Xnow`. Splitting them is what makes a 15-minute refresh affordable — re-deriving floors every quarter hour would recompute numbers that are provably identical.

## Scheduling

**cron-job.org** calls the `Refresh prices` workflow every 15 minutes, weekdays 09:00–16:45 ET, using a **fine-grained token scoped to Actions on this repo alone**. GitHub then fetches, rebuilds, commits, and Pages serves it — about three minutes end to end.

**GitHub's own `schedule:` is a backstop only.** Measured across a full trading day it ran between **7 minutes and 6 hours late** — fine for a nightly job, unusable for prices. It stays in the workflow because it commits the identical file, so it can only help.

An open browser tab cannot update itself — the data is baked into the file. So the page polls with a `HEAD` request every 3 minutes and offers a **reload** when the deployed file has genuinely changed, rather than reloading under someone mid-read. There is also an optional auto-refresh (15 / 30 min); every filter is persisted, so a reload comes back where you were.

## Status / notes (READ before trusting any number)

All of V4's data-hygiene rules apply and live in `backend/screener.py` — bad ticks, zero-volume phantom bars, unadjusted splits (`CHECK LOW`), cash-like instruments, typical-daily-volume rather than one session. Plus four specific to v5:

- **yfinance is an unofficial Yahoo client.** It breaks when Yahoo changes an endpoint — historically a few times a year, usually fixed by `pip install -U yfinance`. No SLA, no support. That is the price of needing no key; V4 on Alpaca is the fallback if it ever breaks badly.
- **Rate limiting is real.** Repeated full scans get throttled: one returned 4,621 names against a normal 6,191 and *overwrote the good floors file*. `do_scan()` now refuses to save a scan returning under 90% of the previous count. The limit clears in a few hours.
- **Weekly bars are labelled by date, not timestamp.** Localizing that label to UTC and converting to ET shifts it back a day, silently keeping the in-progress week — the exact thing the cutoff exists to remove. The symptom is subtle: floors read 4 days old instead of 10, and D% can never go negative. Compare plain dates.
- **`Xnow` above `Xhigh` is arithmetically impossible** from one dataset and means price and history are on different share bases — a split the source never applied to its own bars. Those rows are dropped outright rather than flagged, since they sort straight to the top of FAR on numbers that mean nothing. A 1.5x margin allows for a genuine new high set this week.

**Screening only — no scores, no verdicts, no recommendation.** A large D% means a stock has run far from its floor, not that it is strong. A small one means it is sitting on that floor, not that it is cheap.
