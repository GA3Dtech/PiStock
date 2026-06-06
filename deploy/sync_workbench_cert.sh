#!/usr/bin/env bash
# ======================================================================
#  PiStock — sync the live TLS certificate + server address into the
#  FreeCAD workbench, so the USB drop-in copy trusts THIS server out of
#  the box.
#
#  It copies   ca-cert.pem (the local ROOT CA) -> workbench/pistock_ca.pem
#  and writes  IP:PORT (from pistock.conf)      -> workbench/pistock_host.txt
#
#  (Bundling the CA rather than the server leaf means rotating/regenerating
#   the server cert does NOT require re-syncing every workbench.)
#
#  Idempotent and safe to call from anywhere the cert is created or the
#  server is (re)started: the installer, startapp.sh, startapp_newssl.sh.
#  Never aborts the caller — it just warns and exits 0 if something is
#  missing (no cert yet, no workbench folder...).
#
#  Usage:  sync_workbench_cert.sh [REPO_DIR]
#          (REPO_DIR defaults to the parent of this script's folder)
# ======================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_DIR="${1:-$(cd "$SCRIPT_DIR/.." && pwd)}"

WB="$REPO_DIR/backend/CAD-extensions/pistock-freecad/freecad/pistock_workbench"
# Prefer the local ROOT CA; fall back to the leaf for legacy single-cert
# installs that predate the two-tier PKI.
CERT="$REPO_DIR/ca-cert.pem"
[ -f "$CERT" ] || CERT="$REPO_DIR/cert.pem"
CONF="$REPO_DIR/pistock.conf"

if [ ! -d "$WB" ]; then
  echo "  ! workbench folder not found ($WB) — CA not synced" >&2
  exit 0
fi
if [ ! -f "$CERT" ]; then
  echo "  ! no ca-cert.pem/cert.pem found in $REPO_DIR — workbench CA not synced" >&2
  exit 0
fi

# 1. Bundle the certificate (local CA when available) as the workbench's
#    trusted anchor.
cp -f "$CERT" "$WB/pistock_ca.pem"

# 2. Server address: read pistock.conf (the source of truth the server
#    itself uses); fall back to LAN detection / localhost.
ip=""; port="8000"
if [ -f "$CONF" ]; then
  ip="$(grep -E '^PISTOCK_IP='   "$CONF" | head -n1 | cut -d'"' -f2 || true)"
  p="$( grep -E '^PISTOCK_PORT=' "$CONF" | head -n1 | cut -d'"' -f2 || true)"
  [ -n "$p" ] && port="$p"
fi
[ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -z "$ip" ] && ip="127.0.0.1"
printf '%s:%s\n' "$ip" "$port" > "$WB/pistock_host.txt"

echo "  ✓ workbench CA + host synced ($ip:$port)"
