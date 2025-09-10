#!/bin/bash
cd /Users/jargot/Projects/walkingpad

# Sync dependencies with uv
uv sync

# Run the server with uv
uv run python restserver.py
