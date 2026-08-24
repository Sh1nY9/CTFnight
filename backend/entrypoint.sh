#!/bin/sh
set -eu

exec uvicorn alpha.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips "${ALPHA_FORWARDED_ALLOW_IPS:-127.0.0.1}"
