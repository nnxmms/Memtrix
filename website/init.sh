#!/usr/bin/env bash
set -euo pipefail

# ── Memtrix Website — Initial Setup ──
# Prompts for your domain, obtains a TLS certificate via Certbot,
# and prepares everything for `docker compose up -d`.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "  🧠 Memtrix Website Setup"
echo "  ────────────────────────"
echo ""

# ── Require root (certbot + docker) ──
if [[ $EUID -ne 0 ]]; then
    echo "  ✗ This script must be run as root (sudo ./init.sh)"
    exit 1
fi

# ── Check dependencies ──
for cmd in docker openssl; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  ✗ Required command not found: $cmd"
        exit 1
    fi
done

if ! docker compose version &>/dev/null; then
    echo "  ✗ Docker Compose v2 not found. Install: https://docs.docker.com/compose/install/"
    exit 1
fi

# ── Prompt for domain ──
if [[ -f .env ]]; then
    echo "  ℹ  Existing .env found."
    # shellcheck disable=SC1091
    source .env
    echo "  Current domain: ${DOMAIN:-<not set>}"
    read -rp "  Enter domain (or press Enter to keep): " NEW_DOMAIN
    DOMAIN="${NEW_DOMAIN:-$DOMAIN}"
else
    read -rp "  Enter your domain (e.g. memtrix.example.com): " DOMAIN
fi

if [[ -z "${DOMAIN:-}" ]]; then
    echo "  ✗ Domain cannot be empty."
    exit 1
fi

# Validate domain format (basic check)
if ! [[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]]; then
    echo "  ✗ Invalid domain format: $DOMAIN"
    exit 1
fi

echo ""
echo "  Domain: $DOMAIN"
echo ""

# ── Prompt for Cloudflare API token ──
# Required for DNS-01 challenge (works behind Cloudflare proxy)
if [[ -f certbot/cloudflare.ini ]]; then
    echo "  ℹ  Existing Cloudflare credentials found."
    read -rp "  Re-enter Cloudflare API token? (y/N): " REDO_CF
    if [[ "${REDO_CF,,}" == "y" ]]; then
        read -rsp "  Enter Cloudflare API token: " CF_TOKEN
        echo ""
    fi
else
    echo "  Certbot needs a Cloudflare API token for DNS-01 verification."
    echo "  Create one at: https://dash.cloudflare.com/profile/api-tokens"
    echo "  Required permissions: Zone → DNS → Edit"
    echo ""
    read -rsp "  Enter Cloudflare API token: " CF_TOKEN
    echo ""
fi

# ── Write .env ──
cat > .env <<EOF
DOMAIN=${DOMAIN}
EOF
chmod 600 .env
echo "  ✓ .env written (chmod 600)"

# ── Create directories ──
mkdir -p certbot/conf certbot/www
echo "  ✓ Created certbot directories"

# ── Write Cloudflare credentials ──
if [[ -n "${CF_TOKEN:-}" ]]; then
    cat > certbot/cloudflare.ini <<EOF
dns_cloudflare_api_token = ${CF_TOKEN}
EOF
    chmod 600 certbot/cloudflare.ini
    echo "  ✓ Cloudflare credentials written (chmod 600)"
fi

if [[ ! -f certbot/cloudflare.ini ]]; then
    echo "  ✗ Cloudflare credentials not found at certbot/cloudflare.ini"
    exit 1
fi

# ── Generate DH parameters if missing ──
if [[ ! -f certbot/conf/ssl-dhparams.pem ]]; then
    echo "  ⏳ Generating DH parameters (2048-bit)... this takes a moment."
    openssl dhparam -out certbot/conf/ssl-dhparams.pem 2048 2>/dev/null
    echo "  ✓ DH parameters generated"
else
    echo "  ✓ DH parameters already exist"
fi

# ── Generate recommended SSL options if missing ──
if [[ ! -f certbot/conf/options-ssl-nginx.conf ]]; then
    cat > certbot/conf/options-ssl-nginx.conf <<'SSLCONF'
ssl_session_cache shared:le_nginx_SSL:10m;
ssl_session_timeout 1440m;
ssl_session_tickets off;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
ssl_ciphers "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384";
SSLCONF
    echo "  ✓ SSL options written"
fi

# ── Obtain certificate ──
if [[ -d "certbot/conf/live/$DOMAIN" ]]; then
    echo "  ✓ Certificate already exists for $DOMAIN"
else
    echo "  ⏳ Obtaining TLS certificate for $DOMAIN via DNS-01..."
    echo ""

    docker run --rm \
        -v "$SCRIPT_DIR/certbot/conf:/etc/letsencrypt" \
        -v "$SCRIPT_DIR/certbot/cloudflare.ini:/etc/cloudflare.ini:ro" \
        certbot/dns-cloudflare certonly \
            --dns-cloudflare \
            --dns-cloudflare-credentials /etc/cloudflare.ini \
            --dns-cloudflare-propagation-seconds 30 \
            --email "admin@${DOMAIN}" \
            --agree-tos \
            --no-eff-email \
            --force-renewal \
            -d "$DOMAIN"

    echo ""
    echo "  ✓ Certificate obtained for $DOMAIN"
fi

# ── Generate nginx config from template ──
if [[ ! -f nginx.conf.template ]]; then
    echo "  ✗ nginx.conf.template not found!"
    exit 1
fi

sed "s/\${DOMAIN}/${DOMAIN}/g" nginx.conf.template > nginx.conf
echo "  ✓ nginx.conf generated from template"

# ── Done ──
echo ""
echo "  ✅ Setup complete!"
echo ""
echo "  Start the website:"
echo "    cd website && docker compose up -d"
echo ""
echo "  Your site will be live at:"
echo "    https://$DOMAIN"
echo ""
