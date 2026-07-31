#!/bin/bash
# Sets up (first run only) and launches the screener.
set -e
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "Creating virtual environment (first run only)..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo "No API key needed — market data comes from Yahoo, which requires no signup."
echo "First scan takes 2-3 minutes; prices then refresh on a timer."
echo ""
python app.py
