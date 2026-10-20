"""Convenience launcher: `python run.py` starts the API + dashboard.

Running this script (rather than invoking uvicorn directly) guarantees the
project root is on sys.path so the top-level packages (data, indicators,
strategy, signals, backtest, paper, risk, llm, alerts, api) import
correctly regardless of the current shell's working directory.
"""
import os

import uvicorn

if __name__ == "__main__":
    # Most PaaS platforms (Render, Railway, Heroku, ...) assign a port
    # dynamically via the PORT env var and require the app to bind to it.
    # API_PORT takes precedence for local/manual configuration; PORT is the
    # platform-assigned fallback.
    port = int(os.environ.get("API_PORT") or os.environ.get("PORT") or "8000")
    uvicorn.run(
        "api.main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=port,
        reload=os.environ.get("APP_RELOAD", "false").lower() == "true",
    )
