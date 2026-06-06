#!/usr/bin/env bash
# ======================================================================
#  PiStock — single source of truth for TLS material (two-tier PKI).
#
#  Generates, in REPO_DIR:
#    ca-cert.pem / ca-key.pem   a LOCAL ROOT CA  (created ONCE, then reused)
#    cert.pem    / key.pem      the SERVER leaf  (re-issued on every run,
#                               signed by the CA, with the right SAN + EKU)
#
#  Why two tiers instead of one self-signed cert?
#    - ca-cert.pem is the ONLY thing clients must trust (FreeCAD workbench
#      via pistock_ca.pem, and browsers via "import a Certificate Authority").
#    - Because every server cert is re-signed by the SAME CA, you can rotate
#      the cert or change the server IP WITHOUT redistributing trust.
#    - A single self-signed cert that is also marked CA:TRUE is rejected by
#      browsers (MOZILLA_PKIX_ERROR_CA_CERT_USED_AS_END_ENTITY). The leaf
#      here is CA:FALSE + extendedKeyUsage=serverAuth, which they accept.
#
#  Usage:  gen_certs.sh REPO_DIR IP [DNS]
#  Requires OpenSSL 1.1.1+ (uses -addext).
# ======================================================================
set -euo pipefail

REPO_DIR="${1:?usage: gen_certs.sh REPO_DIR IP [DNS]}"
IP="${2:?usage: gen_certs.sh REPO_DIR IP [DNS]}"
DNS="${3:-pistock.local}"

cd "$REPO_DIR"

CA_DAYS=3650          # root CA: ~10 years (you trust it once)
LEAF_DAYS=825         # server cert: keep < 825d so browsers accept it
CA_CN="PiStock Local CA"

# --- 1. Root CA — create once, then reuse -----------------------------
if [ -f ca-key.pem ] && [ -f ca-cert.pem ]; then
  echo "  = reusing existing local CA (ca-cert.pem)"
else
  openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout ca-key.pem -out ca-cert.pem \
    -days "$CA_DAYS" -sha256 \
    -subj "/CN=${CA_CN}" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
  chmod 600 ca-key.pem
  echo "  + local CA created (ca-cert.pem, ${CA_DAYS}d) — TRUST THIS on clients"
fi

# --- 2. Server leaf — re-issued every run, signed by the CA -----------
openssl req -newkey rsa:4096 -nodes \
  -keyout key.pem -out leaf.csr \
  -subj "/CN=${IP}" >/dev/null 2>&1

cat > leaf.ext <<EOF
subjectAltName = IP:${IP}, DNS:${DNS}, IP:127.0.0.1, DNS:localhost
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF

openssl x509 -req -in leaf.csr \
  -CA ca-cert.pem -CAkey ca-key.pem -CAcreateserial \
  -days "$LEAF_DAYS" -sha256 \
  -extfile leaf.ext -out cert.pem >/dev/null 2>&1

chmod 600 key.pem
rm -f leaf.csr leaf.ext
echo "  + server cert issued (cert.pem, SAN incl. ${IP}/127.0.0.1, signed by local CA)"
