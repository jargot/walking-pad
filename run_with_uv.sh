#!/bin/bash

# Script to run WalkingPad scripts with uv
# Usage: ./run_with_uv.sh <script_name>

cd /Users/jargot/Projects/walkingpad

# Ensure dependencies are synced
echo "📦 Syncing dependencies with uv..."
uv sync

# Run the specified script
if [ "$1" ]; then
    echo "🚀 Running $1 with uv..."
    uv run python "$1"
else
    echo "Usage: $0 <script_name>"
    echo "Examples:"
    echo "  $0 restserver.py"
    echo "  $0 test_connection.py" 
    echo "  $0 test_uv_setup.py"
fi