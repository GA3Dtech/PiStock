#!/usr/bin/env bash
# Genere un certificat auto-signe pour l'IP/host configures, puis lance
# PiStock en HTTPS. Toute la config vient de pistock.conf.
set -euo pipefail

# --- Localiser et charger la configuration ----------------------------
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CONF="$SCRIPT_DIR/../../pistock.conf"   # pistock.conf est a la racine du depot
if [ ! -f "$CONF" ]; then
  echo "ERREUR : configuration introuvable : $CONF" >&2
  echo "Copiez pistock.conf.example -> pistock.conf et renseignez vos valeurs." >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$CONF"
: "${PISTOCK_DIR:?definir PISTOCK_DIR dans pistock.conf}"
: "${PISTOCK_IP:?definir PISTOCK_IP dans pistock.conf}"
PISTOCK_PORT="${PISTOCK_PORT:-8000}"
PISTOCK_DNS="${PISTOCK_DNS:-pistock.local}"

cd "$PISTOCK_DIR"

# 1. (Re)generer le certificat serveur, signe par la CA locale PiStock.
#    gen_certs.sh cree la CA (ca-cert.pem) une seule fois, puis emet a
#    chaque lancement une feuille cert.pem/key.pem valide pour l'IP/host
#    configures (SAN incluant aussi 127.0.0.1 / localhost pour les tests
#    locaux). C'est ca-cert.pem qu'on fait confiance cote clients.
bash "$PISTOCK_DIR/deploy/gen_certs.sh" "$PISTOCK_DIR" "$PISTOCK_IP" "$PISTOCK_DNS"

# 1b. Rafraichir le certificat/adresse embarques dans le workbench FreeCAD
#     pour qu'ils correspondent au cert qu'on vient de generer. Non bloquant.
bash "$PISTOCK_DIR/deploy/sync_workbench_cert.sh" "$PISTOCK_DIR" || true

# 2. Lancer uvicorn en HTTPS
cd backend/app
uvicorn main:app --host 0.0.0.0 --port "$PISTOCK_PORT" \
  --ssl-keyfile ../../key.pem \
  --ssl-certfile ../../cert.pem
