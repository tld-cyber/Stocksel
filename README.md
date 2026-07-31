# Stocksel — 52-week floor

A desktop app that ranks every listed US common stock by how it stands against its 52-week floor.
**No API key. No signup. No account.** Market data comes from Yahoo, which requires none.

Same shape as the World News Globe: a native window (pywebview) over a local Python backend, so all
fetching happens server-side. That is not a stylistic choice — Yahoo sends no CORS headers, so a
browser page cannot call it at all. Doing it in Python is what removes the key requirement entirely.

## What it shows

| | |
|---|---|
| **X**<sub>low</sub> | the 52-week floor, measured through **last Friday** — the current week is excluded, which is what lets price trade *through* it |
| **X**<sub>now</sub> | the current price, shaded by distance to the floor: deep = under it, amber = on it, pale = within 5% |
| **X**<sub>high</sub> | the 52-week ceiling — what it is worth if the floor holds |
| **D%** | X<sub>now</sub> against X<sub>low</sub>; negative means it broke through |
| **held** | days the floor has stood |
| **tch** | touches — weeks price came back within 5%, i.e. how often buyers actually defended it |
| **Vol/day** | typical daily volume (median week ÷ 5), not one session |

Three views: **at / below the floor**, **below the floor**, **furthest above**. Sorted on signed D%,
most negative first.

`held` and `tch` answer different questions and come apart badly. A floor 9 months old that was
touched **once** was never support — it is a single spike-down that happens to be the year's lowest
point. One touched 14 times is a level buyers keep defending. Read `tch` first.

## Running it

```bash
./run.sh          # first run creates a venv and installs deps, then launches
```

or manually:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

The first scan takes about **2 minutes** (6,200 symbols, a year of weekly bars each). After that,
prices refresh every 5 minutes automatically, or on demand with **refresh prices**. Floors are
weekly data and are deliberately *not* recomputed intraday — they cannot change during a session.

Backend alone, without the window:

```bash
python -m backend.server
curl "http://127.0.0.1:5714/api/status"
```

## Packaging into a single .exe / .app

Must be run **on the target OS** — PyInstaller cannot cross-compile.

```bash
source venv/bin/activate
pyinstaller --onefile --windowed --name "Stocksel" --add-data "frontend:frontend" app.py
```

On Windows, `--add-data` uses a semicolon: `--add-data "frontend;frontend"`.

Output lands in `dist/`. That single file is what you share — double-click, window opens, no
terminal, no install, nothing to configure.

`.github/workflows/build.yml` builds **Windows and Apple Silicon Mac** on every push to `main`.
Intel Mac is deliberately not built — add a job only if someone on `x86_64` actually needs it,
since PyInstaller cannot cross-compile and an arm64 build will not run there.

## Sharing it

Same gotchas as any unsigned app:

- **Architecture (Mac).** Only Apple Silicon is built. On an Intel Mac the `.app` fails with
  *"not supported on this Mac"* — that message means a chip mismatch, not a corrupt download.
  Have them run `uname -m` first: `arm64` is fine, `x86_64` needs a build job adding.
- **Gatekeeper.** Unsigned, so macOS blocks it after download: **System Settings → Privacy &
  Security → Open Anyway**, or `xattr -cr path/to/Stocksel.app`. Windows shows a SmartScreen
  "unknown publisher" prompt — More info → Run anyway.
- **Gmail blocks `.app`/`.exe` attachments**, even zipped. Use Drive or similar.

## Known limits

**yfinance is an unofficial Yahoo client.** It breaks occasionally when Yahoo changes an endpoint —
historically a few times a year, usually fixed by `pip install -U yfinance`. There is no paid API
behind this and no SLA. That is the price of needing no key.

**Delisted and thin symbols** produce noisy console warnings during a scan (`possibly delisted; no
price data found`). Harmless — those names are simply skipped.

**Data hygiene already applied**, each found by reading the bars behind a name that had ranked #1:

- bad ticks — a bar whose low is under 25% of its own close is a misprint, not a tradeable price
- zero-volume phantom bars — all four OHLC equal, no trade behind them
- unadjusted splits — flagged **CHECK LOW** via a 14–86× week-over-week jump that no real move
  produces; a real move accumulates, a split teleports
- warrants, units, rights, ETFs, and instruments whose whole year spans under 25%
- **regular-session prices only** — a last-trade including extended hours lets one thin after-hours
  print set X<sub>now</sub>. Measured on a real board: 29 of 400 names had a last print more than 5%
  off their own close, one of them 68% off, every one in the closing seconds of the after-hours
  session. They would have rendered as breakdowns that never happened.
