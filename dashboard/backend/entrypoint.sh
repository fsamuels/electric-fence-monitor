#!/bin/sh
set -eu

case "$1" in
  api)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  ingest)
    exec python -m app.ingest
    ;;
  *)
    echo "usage: entrypoint.sh {api|ingest}" >&2
    exit 1
    ;;
esac
