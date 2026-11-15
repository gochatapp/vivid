#!/bin/bash

# Prefer the local .venv, fall back to a plain venv folder
PY="./.venv/bin/python"
[ -x "$PY" ] || PY="./venv/bin/python"

# Check for --background flag
if [[ "$1" == "--background" ]]; then
    shift
    # Run in background, redirect output to nohup.out
    nohup "$PY" vivid.py "$@" > nohup.out 2>&1 &
    echo "Vivid started in background (PID: $!)"
    echo "Output logged to nohup.out"
else
    # Print startup message
    echo "Vivid Chat starting..."
    echo "To run in the background, use: ./vlin.sh --background"
    # Run in foreground
    "$PY" vivid.py "$@"
fi
