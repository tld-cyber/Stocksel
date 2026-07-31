"""
Data layer for the 52-week floor screener — Yahoo via yfinance, NO API KEY.

Why not Alpaca, which the desktop version of this originally used: Alpaca needs a key per person,
and its free tier delays prices 15 minutes. Running the fetch server-side (the whole point of the
desktop-app shape) means we are not restricted to APIs that publish CORS headers, so Yahoo becomes
available — no signup, no key, no card, and live prices. Recipients install nothing and configure
nothing. The trade is that yfinance is an unofficial client and breaks occasionally when Yahoo
changes an endpoint; a `pip install -U yfinance` is the usual fix.

Two phases, deliberately split because they age at completely different rates:

  scan()     the slow one (~2-3 min for ~7k names). Weekly bars for a year -> the floor, its date,
             how long it has held, how often it was touched, the ceiling, typical volume. NONE of
             this can change during a trading day; it is weekly data.
  refresh()  the fast one (seconds). Latest price for the visible pool only. This is the only
             number that moves intraday, so it is the only one worth re-fetching on a timer.
"""
import io
import math
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytz
import yfinance as yf

ET = pytz.timezone("America/New_York")

BAR_CHUNK   = 400        # symbols per yfinance call; 400 measured at ~52 symbols/sec, no throttling
PRICE_CHUNK = 500        # daily bars are light; larger chunks cut round-trips

# ── data hygiene ─────────────────────────────────────────────────────────────
# Each of these was found by reading the actual bars behind a name that had ranked #1, and each
# removes bad data rather than filtering real results.
BAD_TICK_RATIO = 0.25    # ignore a bar's low if it is under 25% of that same bar's close — a low of
                         # $0.0026 against a $1.77 close is a misprint, not a price you could trade
MIN_BAR_VOL    = 1       # zero-volume bars are placeholders with all four OHLC equal; no trade backs them
SUSPECT_RATIO  = 20.0    # low more than 20x under the year's median close -> different share basis
JUMP_RATIO     = 4.0     # week-over-week close jump this big is a split, not a move. Measured: real
                         # runs stay under 4x even at +3,000% for the year, splits land at 14-86x.
                         # A real move accumulates; a split teleports.
TOUCH_BAND     = 1.05    # a week whose low came within 5% of the floor counts as a touch
FLAT_RANGE_PCT = 25.0    # a year spanning less than this is a cash-like instrument, not a stock


def _is_common_stock(sym: str) -> bool:
    """Drop warrant / unit / rights classes. A 5-letter US ticker ending W, U or R (and the odd Z)
    is a derivative of the common stock, not the stock — and being low-priced they generate absurd
    percentages that crowd out real names."""
    return not (len(sym) == 5 and sym[-1] in ("W", "U", "R", "Z"))


def universe(progress=lambda s: None):
    """All listed US common stocks, from Nasdaq's public symbol file. No key, no auth.

    Better than deriving it from a broker's asset list: this file carries an explicit ETF flag, so
    Treasury and bond funds are excluded by classification rather than by inferring "this is a fund"
    from the fact that it barely moves.
    """
    progress("fetching symbol list")
    req = urllib.request.Request("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt",
                                 headers={"User-Agent": "Mozilla/5.0"})
    txt = urllib.request.urlopen(req, timeout=30).read().decode("latin-1").splitlines()
    hdr = txt[0].split("|")
    i_sym, i_etf, i_test = hdr.index("Symbol"), hdr.index("ETF"), hdr.index("Test Issue")
    out = []
    for line in txt[1:]:
        r = line.split("|")
        if len(r) <= max(i_sym, i_etf, i_test):
            continue
        s = r[i_sym].strip()
        if r[i_etf] == "N" and r[i_test] == "N" and s.isalpha() and 1 <= len(s) <= 5 \
                and _is_common_stock(s):
            out.append(s)
    return sorted(set(out))


def _week_cutoff():
    """Monday 00:00 ET of the current week, tz-aware.

    The floor is measured through LAST week on purpose. Over a window that includes today the low is
    self-referential — a stock making a new low simply redefines the floor, so Xnow >= Xlow always
    and D% could never go negative. Cutting here makes Xlow a level that actually held, which is what
    lets price trade through it and lets "what broke down" return anything at all.
    """
    now = datetime.now(ET)
    d = now.date() - timedelta(days=now.weekday())
    return ET.localize(datetime(d.year, d.month, d.day))


def _weekly(symbols):
    """Weekly OHLCV for a year. auto_adjust=False is deliberate: that leaves prices split-adjusted
    but NOT dividend-adjusted, so a floor is a price the stock actually traded at. auto_adjust=True
    back-adjusts dividends and drifts the low off any real level (AAPL 200.70 vs a traded 201.50)."""
    return yf.download(symbols, period="1y", interval="1wk", auto_adjust=False,
                       progress=False, threads=True, group_by="column")


def scan(progress=lambda s: None):
    """Full scan -> (rows, meta). Slow; everything here is weekly and stable through the day."""
    t0 = time.time()
    syms = universe(progress)
    progress(f"{len(syms):,} symbols — fetching a year of weekly bars")

    cutoff = _week_cutoff()
    rows, done = [], 0
    for i in range(0, len(syms), BAR_CHUNK):
        chunk = syms[i:i + BAR_CHUNK]
        try:
            df = _weekly(chunk)
        except Exception as e:
            progress(f"chunk failed ({type(e).__name__}) — continuing")
            continue
        if df is None or df.empty:
            continue
        rows.extend(_rows_from(df, chunk, cutoff))
        done += len(chunk)
        progress(f"scanned {done:,} / {len(syms):,}")

    rows.sort(key=lambda r: r["d"])          # signed D% ascending: deepest break first
    meta = {
        "generated":  datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET"),
        "scanned":    len(rows),
        "below":      sum(1 for r in rows if r["d"] < 0),
        "floor_asof": cutoff.strftime("%Y-%m-%d"),
        "source":     "Yahoo Finance",
        "took":       round(time.time() - t0, 1),
    }
    progress("")
    return rows, meta


def _rows_from(df, chunk, cutoff):
    """Turn one yfinance frame into row dicts. Vectorised per column — a per-symbol loop over
    ~7k names is minutes of pandas overhead on its own."""
    try:
        low, high, close, vol = df["Low"], df["High"], df["Close"], df["Volume"]
    except Exception:
        return []
    if isinstance(low, pd.Series):                      # single-symbol frame has flat columns
        low, high, close, vol = (x.to_frame(chunk[0]) for x in (low, high, close, vol))

    # yfinance labels a weekly bar with its start DATE, naive and with no meaningful clock time.
    # Treat it as a plain date. Localizing it to UTC and converting to ET (the obvious-looking move)
    # shifts 2026-07-27 back to 2026-07-26 20:00 ET, which slips under a Monday-00:00 ET cutoff and
    # silently KEEPS the in-progress week — the exact thing the cutoff exists to remove. The symptom
    # is subtle: floors read 4 days old instead of 10, and D% can never go negative.
    idx = pd.DatetimeIndex(low.index)
    if idx.tz is not None:
        idx = idx.tz_convert(None)
    keep = idx.normalize() < pd.Timestamp(cutoff.date())
    if not keep.any():
        return []
    low, high, close, vol = low[keep], high[keep], close[keep], vol[keep]

    out = []
    now = datetime.now(timezone.utc)
    for s in chunk:
        if s not in low.columns:
            continue
        lo, hi, cl, vl = low[s].dropna(), high[s], close[s], vol[s]
        if lo.empty:
            continue
        good = lo[(vl.reindex(lo.index).fillna(0) >= MIN_BAR_VOL)
                  & (lo >= cl.reindex(lo.index) * BAD_TICK_RATIO)]
        if good.empty:
            continue
        floor = float(good.min())
        if floor <= 0 or not math.isfinite(floor):
            continue
        ceiling = float(hi.max())
        med_close = float(cl.median())
        # A split shows as a discontinuous week-over-week close ratio; a real move never does.
        ratios = (cl / cl.shift(1)).replace([float("inf")], float("nan")).dropna()
        max_jump = float(ratios.max()) if len(ratios) else 0.0
        when = good.idxmin()
        out.append({
            "sym": s,
            "x":   round(floor, 4),
            "xh":  round(ceiling, 4),
            "xd":  when.strftime("%Y-%m-%d"),
            "hd":  max(0, (now - when.to_pydatetime().replace(tzinfo=timezone.utc)).days),
            "t":   int((good <= floor * TOUCH_BAND).sum()),
            "w":   int(len(lo)),
            "v":   int(float(vl.median()) / 5) if len(vl.dropna()) else 0,   # typical DAILY volume
            "f":   bool(ceiling > 0 and (ceiling - floor) / floor * 100 < FLAT_RANGE_PCT),
            "s":   bool((med_close > 0 and floor < med_close / SUSPECT_RATIO) or max_jump >= JUMP_RATIO),
            "y":   round(float(cl.dropna().iloc[-1]), 4) if len(cl.dropna()) else round(floor, 4),
            "d":   0.0,
        })
    for r in out:                                        # seed D% off the last weekly close
        r["d"] = round((r["y"] - r["x"]) / r["x"] * 100, 2)
    return out


def refresh(symbols, progress=lambda s: None):
    """Latest price for the given symbols -> {sym: price}. Seconds, not minutes.

    Only Xnow moves intraday, so this is all the timer needs to re-fetch.

    Daily bars, not 1-minute: one row per symbol instead of ~390 for the same single number, which
    measured faster (78/sec vs 62) AND returned more symbols (497 vs 483 of 500). They are also
    regular-session, which matters more than the speed — a last-trade that includes extended hours
    lets one thin after-hours print set the price. Measured on a real board: 29 of 400 names had a
    last print more than 5% off their own close, one of them 68% off, all in the closing seconds of
    the after-hours session. Those would render as breakdowns through the floor that never happened.
    """
    out = {}
    syms = list(symbols)
    for i in range(0, len(syms), PRICE_CHUNK):
        chunk = syms[i:i + PRICE_CHUNK]
        try:
            df = yf.download(chunk, period="2d", interval="1d", auto_adjust=False,
                             progress=False, threads=True, group_by="column")
        except Exception:
            continue
        if df is None or df.empty:
            continue
        cl = df["Close"]
        if isinstance(cl, pd.Series):
            cl = cl.to_frame(chunk[0])
        last = cl.ffill().iloc[-1] if len(cl) else None
        if last is None:
            continue
        for s in chunk:
            try:
                v = float(last[s])
                if math.isfinite(v) and v > 0:
                    out[s] = round(v, 4)
            except Exception:
                pass
        progress(f"prices {min(i + PRICE_CHUNK, len(syms)):,} / {len(syms):,}")
    progress("")
    return out


def apply_prices(rows, prices):
    """Fold refreshed prices back in and re-derive D%. Xlow and everything from it stays put —
    it is weekly data and re-deriving it intraday would be wrong, not merely wasteful."""
    for r in rows:
        p = prices.get(r["sym"])
        if p:
            r["y"] = p
            r["d"] = round((p - r["x"]) / r["x"] * 100, 2)
    rows.sort(key=lambda r: r["d"])
    return rows
