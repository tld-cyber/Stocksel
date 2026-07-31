"""
Desktop launcher for the 52-week floor screener.

Starts the local Flask backend (backend/screener.py does the fetching) in a background thread, then
opens a native OS window pointed at it via pywebview — no browser, no address bar, no install for
whoever you hand it to. This is the file PyInstaller packages into the single-file .exe / .app.

Run directly with `python app.py` during development. The backend can be exercised headlessly on
its own — see backend/server.py's __main__ block.
"""
import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import server as backend_server

HOST = "127.0.0.1"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 860


def find_free_port():
    """Bind to port 0 and let the OS pick. Avoids colliding with anything already listening —
    including a second copy of this app."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


def main():
    port = find_free_port()
    threading.Thread(
        target=backend_server.run,
        kwargs={"host": HOST, "port": port, "debug": False},
        daemon=True,
    ).start()

    import webview  # after the thread starts, so a missing dep fails fast via requirements.txt
    webview.create_window(
        "Stocksel — 52-week floor",
        f"http://{HOST}:{port}/",
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(900, 600),
    )
    # private_mode=False: pywebview's default (True) wipes the WKWebView's storage on every window
    # creation on macOS, which triggers a fresh Keychain prompt ("WebCrypto Master Key") each launch.
    # Nothing here depends on per-window wiping — the scan lives in the backend's memory, not in
    # browser storage — so turning it off just spares the recipient a confusing system dialog.
    webview.start(private_mode=False)


if __name__ == "__main__":
    main()
