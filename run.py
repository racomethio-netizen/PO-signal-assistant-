"""Convenience launcher: `python run.py` starts the API + dashboard.

Running this script (rather than invoking uvicorn directly) guarantees the
project root is on sys.path so the top-level packages (data, indicators,
strategy, signals, backtest, paper, risk, llm, alerts, api) import
correctly regardless of the current shell's working directory.
"""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host=os.environ.get("API_HOST", "0.0.0.0"),
        port=int(os.environ.get("API_PORT", "8000")),
        reload=os.environ.get("APP_RELOAD", "false").lower() == "true",
    )
