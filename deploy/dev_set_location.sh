#!/usr/bin/env bash
# ======================================================================
#  PiStock — dev helper: re-point the config when you change location.
#
#  Updates, in one go, the files that carry the server IP/port:
#    - pistock.conf                       (server side: PISTOCK_IP/PORT)
#    - the FreeCAD workbench pistock_host.txt   (what the macros target)
#    - the TLS certificate / pistock_ca.pem     (SAN must match the IP)
#
#  Two modes:
#    1) LOCAL  — run the server on THIS machine (debug): auto-detect the
#                LAN IP, regenerate the cert for it (+127.0.0.1/localhost),
#                update pistock.conf and the workbench.
#    2) EXTERNAL — point the FreeCAD macros at another PiStock server:
#                enter its IP/port; optionally fetch & trust its cert.
#
#  Run as your normal user (no sudo needed); from anywhere in the repo.
# ======================================================================
set -euo pipefail

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m  ✓ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m  ! %s\033[0m\n' "$*"; }

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONF="$REPO_DIR/pistock.conf"
WB="$REPO_DIR/backend/CAD-extensions/pistock-freecad/freecad/pistock_workbench"
cd "$REPO_DIR"

detect_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p' | head -n1 || true)"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "${ip:-127.0.0.1}"
}

conf_get() {  # conf_get KEY DEFAULT
  local v=""
  [ -f "$CONF" ] && v="$(grep -E "^$1=" "$CONF" | head -n1 | cut -d'"' -f2 || true)"
  echo "${v:-$2}"
}

cur_port="$(conf_get PISTOCK_PORT 8000)"
cur_dns="$(conf_get PISTOCK_DNS pistock.local)"

say "PiStock — change location"
echo "  1) Local server on this machine (debug)"
echo "  2) Point macros at an external PiStock server"
read -rp "Choice [1]: " choice
choice="${choice:-1}"

read -rp "Port [$cur_port]: " port
port="${port:-$cur_port}"

if [ ! -d "$WB" ]; then
  warn "workbench folder not found ($WB) — host/cert won't be updated there"
fi

# ----------------------------------------------------------------------
if [ "$choice" = "2" ]; then
  # ---------- EXTERNAL ----------
  read -rp "External server IP or hostname: " ip
  [ -z "$ip" ] && { echo "No IP given, aborting." >&2; exit 1; }

  if [ -d "$WB" ]; then
    printf '%s:%s\n' "$ip" "$port" > "$WB/pistock_host.txt"
    ok "workbench pistock_host.txt -> $ip:$port"
  fi

  read -rp "Fetch and TRUST that server's TLS certificate now? [y/N]: " yn
  if [ "${yn:-N}" = "y" ] || [ "${yn:-N}" = "Y" ]; then
    if openssl s_client -connect "$ip:$port" -servername "$ip" </dev/null 2>/dev/null \
         | openssl x509 > "$WB/pistock_ca.pem" 2>/dev/null && [ -s "$WB/pistock_ca.pem" ]; then
      ok "fetched cert -> pistock_ca.pem (trust-on-first-use)"
      warn "you trusted whatever the server presented — only do this on a network you trust"
    else
      rm -f "$WB/pistock_ca.pem"
      warn "could not fetch the certificate (server down? wrong port?). Copy it manually if self-signed."
    fi
  else
    warn "if that server uses a self-signed cert, copy its cert.pem to $WB/pistock_ca.pem"
  fi
  say "Done — FreeCAD macros now target https://$ip:$port"
  exit 0
fi

# ---------- LOCAL ----------
def_ip="$(detect_ip)"
read -rp "Local IP [$def_ip] (or 127.0.0.1 for pure local): " ip
ip="${ip:-$def_ip}"

# 1) pistock.conf (create from example if missing)
if [ ! -f "$CONF" ] && [ -f "$REPO_DIR/pistock.conf.example" ]; then
  cp "$REPO_DIR/pistock.conf.example" "$CONF"
fi
if [ -f "$CONF" ]; then
  sed -i -E "s|^PISTOCK_IP=.*|PISTOCK_IP=\"$ip\"|"     "$CONF" || true
  sed -i -E "s|^PISTOCK_PORT=.*|PISTOCK_PORT=\"$port\"|" "$CONF" || true
  grep -qE "^PISTOCK_DIR=" "$CONF" || printf 'PISTOCK_DIR="%s"\n' "$REPO_DIR" >> "$CONF"
  ok "pistock.conf -> IP=$ip PORT=$port"
else
  printf 'PISTOCK_DIR="%s"\nPISTOCK_IP="%s"\nPISTOCK_DNS="%s"\nPISTOCK_PORT="%s"\n' \
    "$REPO_DIR" "$ip" "$cur_dns" "$port" > "$CONF"
  ok "pistock.conf created (IP=$ip PORT=$port)"
fi

# 2) regenerate the TLS cert for this IP (+127.0.0.1/localhost)
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \
  -days 825 -nodes \
  -subj "/CN=$ip" \
  -addext "subjectAltName=IP:$ip,DNS:$cur_dns,IP:127.0.0.1,DNS:localhost" 2>/dev/null
chmod 600 key.pem
ok "TLS certificate regenerated (CN=$ip, SAN incl. 127.0.0.1/localhost)"

# 3) update the workbench (host + bundled CA)
if [ -d "$WB" ]; then
  printf '%s:%s\n' "$ip" "$port" > "$WB/pistock_host.txt"
  cp -f cert.pem "$WB/pistock_ca.pem"
  ok "workbench updated (host=$ip:$port, CA refreshed)"
fi

# 4) offer to restart the service if it exists
if systemctl is-active --quiet pistock 2>/dev/null; then
  read -rp "Restart the 'pistock' service now? [Y/n]: " r
  if [ "${r:-Y}" != "n" ] && [ "${r:-Y}" != "N" ]; then
    sudo systemctl restart pistock && ok "service restarted"
  fi
else
  warn "restart the server so it serves the new cert (e.g. systemctl restart pistock,"
  warn "or relaunch backend/app/startapp.sh)"
fi

say "Done — local PiStock at https://$ip:$port"
warn "On other FreeCAD machines, re-copy the workbench (cert changed)."
