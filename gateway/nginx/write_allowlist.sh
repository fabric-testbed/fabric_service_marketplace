#!/usr/bin/env bash
# Turn GATEWAY_ALLOWED_SOURCES (from gateway.env) into Nginx allow/deny rules,
# so callers other than the portal are refused before reaching gateway.py.
# Usage: write_allowlist.sh <output file>   (run with gateway.env already sourced)
set -euo pipefail
OUT="$1"
: "${GATEWAY_ALLOWED_SOURCES:?GATEWAY_ALLOWED_SOURCES is not set in gateway.env}"
{
    echo "# Generated from GATEWAY_ALLOWED_SOURCES in gateway.env — do not edit by hand."
    IFS=',' read -ra SOURCES <<< "$GATEWAY_ALLOWED_SOURCES"
    for src in "${SOURCES[@]}"; do
        src="$(echo "$src" | xargs)"
        [ -n "$src" ] && echo "allow $src;"
    done
    echo "deny all;"
} | sudo tee "$OUT" > /dev/null
