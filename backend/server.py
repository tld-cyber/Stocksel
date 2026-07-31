"""
Local Flask backend for the 52-week floor screener.

Serves the single-file frontend and a small JSON API. All fetching happens here, server-side —
which is the whole reason this is a desktop app rather than a shared HTML file: Yahoo sends no CORS
headers, so a browser page cannot call it at all, while Python can. That also means no API key for
anyone. Runs only on 127.0.0.1, never exposed to the network.

Two endpoints because the data has two clocks:
  /api/data      the full scan — weekly floors. Slow (~2-3 min), stable all day, cached.
  /api/prices    just Xnow for the visible names. Seconds. This is what the timer hits.
"""
import os
import sys
import threading
import time

from flask import Flask, jsonify, request, send_from_directory

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Once PyInstaller freezes this into a onefile build, __file__ resolves inside the temporary
# extraction dir (sys._MEIPASS) that is recreated on every launch. PROJECT_DIR is still right for
# the bundled read-only frontend/ (that is exactly where --add-data extracts it), but anything a
# person needs to find and edit has to live next to the executable instead.
_DATA_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else PROJECT_DIR
FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")

from . import screener

app = Flask(__name__, static_folder=None)

_state = {
    "rows": None,        # last full scan
    "meta": None,
    "stage": "",         # human-readable progress, polled by the frontend
    "scanning": False,
    "refreshed": None,   # when Xnow was last updated
}
_lock = threading.Lock()


def _progress(stage):
    with _lock:
        _state["stage"] = stage


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(FRONTEND_DIR, filename)


@app.route("/api/status")
def api_status():
    """Polled while a scan runs so the window can show real stages instead of a spinner."""
    with _lock:
        return jsonify({"stage": _state["stage"], "scanning": _state["scanning"],
                        "hasData": _state["rows"] is not None,
                        "meta": _state["meta"], "refreshed": _state["refreshed"]})


def _run_scan():
    """Full scan, then an immediate price refresh.

    The refresh is not optional. scan() seeds Xnow from the last completed WEEKLY close, because the
    current week is excluded from the floor — so without this a name that moved sharply this week
    shows a stale price and a wrong D%. Measured: KPTI seeded at 7.41 against a real 7.00.
    """
    with _lock:
        if _state["scanning"]:
            return
        _state["scanning"] = True
    try:
        rows, meta = screener.scan(_progress)
        # Price only the two ends the UI can actually show, not all ~6,200. Rows arrive sorted by
        # D% ascending, so the head is everything at or through the floor and the tail is the
        # furthest-above view; the vast middle is never rendered. Pricing the whole universe here
        # ran straight into Yahoo throttling after the 6,200 weekly requests that precede it, and
        # took longer than the entire scan. Everything unpriced keeps its seeded weekly close, and
        # the timer tops up whatever is on screen.
        _progress("fetching current prices")
        ends = [r["sym"] for r in rows[:1200]] + [r["sym"] for r in rows[-400:]]
        prices = screener.refresh(ends, _progress)
        rows = screener.apply_prices(rows, prices)
        meta["priced"] = len(prices)
        # Recount AFTER pricing. scan() computes this against Xnow seeded from the last weekly
        # close, and that close can never sit below the floor derived from the same bars — so the
        # count is structurally 0 at that point. Only a live price can be under the floor, which is
        # the entire point of the below-the-floor view. Reported 0 while the table showed -51%.
        meta["below"] = sum(1 for r in rows if r["d"] < 0)
        with _lock:
            _state["rows"], _state["meta"] = rows, meta
            _state["refreshed"] = time.strftime("%H:%M:%S")
    except Exception as e:
        _progress(f"scan failed: {type(e).__name__}: {e}")
        print(f"[scan] FAILED {type(e).__name__}: {e}", flush=True)
    finally:
        with _lock:
            _state["scanning"] = False
        print(f"[scan] done — {(_state['meta'] or {}).get('scanned', 0)} names, "
              f"{(_state['meta'] or {}).get('below', 0)} below their floor", flush=True)


@app.route("/api/data")
def api_data():
    """The full scan. Kicks one off if there is none yet; returns what exists otherwise."""
    with _lock:
        have, scanning = _state["rows"] is not None, _state["scanning"]
    if request.args.get("rescan") == "1" or (not have and not scanning):
        threading.Thread(target=_run_scan, daemon=True).start()
        with _lock:
            if _state["rows"] is None:
                return jsonify({"pending": True, "stage": _state["stage"]})
    with _lock:
        if _state["rows"] is None:
            return jsonify({"pending": True, "stage": _state["stage"]})
        return jsonify({"rows": _state["rows"], "meta": _state["meta"],
                        "refreshed": _state["refreshed"]})


@app.route("/api/prices")
def api_prices():
    """Xnow only, for the names the page is actually showing.

    Scoped to a symbol list rather than the whole universe because this runs on a timer — refetching
    7,000 names every few minutes to update 20 visible rows would be pointless load. Xlow and
    everything derived from it is weekly and deliberately NOT recomputed here.
    """
    syms = [s for s in request.args.get("symbols", "").split(",") if s]
    with _lock:
        rows = _state["rows"]
    if not rows:
        return jsonify({"error": "no scan yet"}), 409
    if not syms:
        syms = [r["sym"] for r in rows[:600]]
    prices = screener.refresh(syms)
    with _lock:
        screener.apply_prices(_state["rows"], prices)
        _state["refreshed"] = time.strftime("%H:%M:%S")
        if _state["meta"]:                      # keep the headline count honest as prices move
            _state["meta"]["below"] = sum(1 for r in _state["rows"] if r["d"] < 0)
        return jsonify({"prices": prices, "refreshed": _state["refreshed"],
                        "meta": _state["meta"], "rows": _state["rows"]})


def create_app():
    return app


def run(host="127.0.0.1", port=5714, debug=False):
    # threaded=True is load-bearing, not a nicety: Flask's dev server handles ONE request at a time
    # by default, and a full scan takes minutes. Without it, /api/status could not be polled while
    # the scan it is reporting on was running — the progress line would freeze at its first value
    # and the window would look hung for the entire scan.
    app.run(host=host, port=port, debug=debug, use_reloader=False, threaded=True)


if __name__ == "__main__":
    run(debug=True)
