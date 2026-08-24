"""Windows desktop launcher used by the packaged executable."""
from __future__ import annotations

import threading
import webbrowser

import uvicorn


def open_browser() -> None:
    webbrowser.open("http://127.0.0.1:8000/")


if __name__ == "__main__":
    from app.main import app
    threading.Timer(1.2, open_browser).start()
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
