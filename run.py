#!/usr/bin/env python3
"""PIPEZ entrypoint. PIPEZ_MOCK=1 runs without an API key."""
import uvicorn

from pipez import config

if __name__ == "__main__":
    uvicorn.run("pipez.server:app", host=config.HOST, port=config.PORT)
