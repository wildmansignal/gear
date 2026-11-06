#!/usr/bin/env bash
# Deploy the Gear Pro gateway to the droplet (nginx + certbot + systemd, same pattern as the other apps).
set -euo pipefail
HOST=${HOST:-root@167.172.142.0}
DOMAIN=${DOMAIN:-gear.167-172-142-0.sslip.io}
PORT=${PORT:-5500}
DIR=/opt/gear-gateway
HERE="$(cd "$(dirname "$0")" && pwd)"

rsync -az --exclude venv --exclude data --exclude .env --exclude __pycache__ "$HERE/" "$HOST:$DIR/"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd $DIR
# a venv copied from a Mac (older deploy.sh) cannot run here; rebuild it
[ -x venv/bin/python ] && venv/bin/python -c "" 2>/dev/null || { rm -rf venv; python3 -m venv venv; }
venv/bin/pip install -q -r requirements.txt
mkdir -p data/logs
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^ADMIN_TOKEN=.*/ADMIN_TOKEN=\$(openssl rand -hex 24)/" .env
  # reuse the OpenRouter key already on this box if present
  if [ -f /opt/slop-remake/.env ]; then
    OR=\$(grep '^OPENROUTER_API_KEY=' /opt/slop-remake/.env | cut -d= -f2-)
    [ -n "\$OR" ] && sed -i "s#^OPENROUTER_API_KEY=.*#OPENROUTER_API_KEY=\$OR#" .env
  fi
  echo "NOTE: new .env written at $DIR/.env; fill in Stripe + Resend values."
fi
cat > /etc/systemd/system/gear-gateway.service <<UNIT
[Unit]
Description=Gear Pro gateway (OpenAI-compatible proxy + Stripe keys)
After=network.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
Environment=PATH=$DIR/venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$DIR/venv/bin/gunicorn -k gthread -w 2 --threads 16 -b 127.0.0.1:$PORT --timeout 900 gateway:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now gear-gateway
systemctl restart gear-gateway
if [ ! -f /etc/nginx/sites-enabled/gear-gateway ]; then
cat > /etc/nginx/sites-available/gear-gateway <<NGINX
server {
    server_name $DOMAIN;
    client_max_body_size 50m;
    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Forwarded-For \\\$remote_addr;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 900s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
    listen 80;
}
NGINX
ln -sf /etc/nginx/sites-available/gear-gateway /etc/nginx/sites-enabled/gear-gateway
nginx -t && systemctl reload nginx
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --register-unsafely-without-email --redirect || echo "certbot failed; site is on http only"
fi
sleep 1
curl -s http://127.0.0.1:$PORT/health || true
echo
systemctl --no-pager --lines=5 status gear-gateway | head -12
REMOTE
echo "deployed: https://$DOMAIN/health"
