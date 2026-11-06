#!/usr/bin/env python3

"""Application entry point: wire the configured repository and run Flask."""

from app import create_app
from repository import get_repository

app = create_app(get_repository())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
