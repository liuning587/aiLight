#!/usr/bin/env python3
"""PyInstaller entry point for lightd."""

from tools.lightd.server import run_server

if __name__ == "__main__":
    run_server()
