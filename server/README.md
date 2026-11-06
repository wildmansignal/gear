# Gear Pro gateway

The server side of Gear Pro. A small Flask app that:

- proxies `/v1/chat/completions` to OpenRouter with the subscriber's key checked and metered (30-day budget, resets on Stripe renewal),
- answers `/user/info` so the app can show credits,
- serves the help bot, community templates and bug-report log uploads,
- turns Stripe subscriptions into keys (`/stripe/webhook`) and shows the key after checkout (`/activate`),
- lets you mint keys by hand (`POST /admin/keys` with `X-Admin-Token`).

## Run locally

```bash
cd server
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY at least
venv/bin/python gateway.py          # http://127.0.0.1:5500
```

Point the app at it with `npm run dev:gateway` (sets `GEAR_GATEWAY_URL=http://localhost:5500/v1`).

Mint a test key:

```bash
curl -s -X POST http://127.0.0.1:5500/admin/keys -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' -d '{"email":"you@example.com","note":"test"}'
```

## Deploy

`./deploy.sh` rsyncs to the droplet, creates the venv, systemd unit `gear-gateway`, and the nginx site `gear.167-172-142-0.sslip.io` with a Let's Encrypt cert. Then edit `/opt/gear-gateway/.env` on the droplet and `systemctl restart gear-gateway`.

## Stripe

`python stripe_setup.py` (with `STRIPE_SECRET_KEY` set) creates the Gear Pro product, monthly + yearly prices and two Payment Links whose success page is `/activate`. Register the webhook it prints and put the signing secret in `.env`.

## Models

`models.json` maps the names the app sends to OpenRouter model ids. `dyad/auto` is the default model every Pro user gets; change it there.
