# GEAR: the playbook

Gear is Dan's vibe-coding desktop app, sold from https://danplants.com/gear. This file is the operator's checklist: how it is built, how it ships, how money flows, and what is still on Dan.

## What it is

- A fork of Dyad v0.26.0 (Apache 2.0, see NOTICE and CHANGES.md). Electron + React + Vite. Branch `gear` in this repo.
- Free download, bring-your-own AI key. **Gear Pro** = a key from Dan's gateway that gives model access with a monthly budget.
- Brand constants: `src/brand.ts` (name, links, gateway URL). Rename or re-point everything from there.
- Apps users build live in `~/gear-apps`. Deep links use `gear://`.

## Build and release

```bash
npm ci                 # once
npm start              # dev
npm run make           # installers in out/make (DMG + ZIP on this Mac, arm64)
./release.sh 1.0.0     # pushes the branch and publishes out/make/* as a GitHub release (needs gh auth)
```

- Version lives in `package.json`. Bump it before `npm run make`; the DMG name carries it.
- The sales page downloads from `https://github.com/wildmansignal/gear/releases/latest/download/Gear-<version>-arm64.dmg`. Keep that file name pattern, or change `DOWNLOADS` in `~/danplants-web/gear.html` and `GEAR_DOWNLOAD_*` env on Vercel.
- Mac builds are unsigned until Dan has an Apple Developer ID ($99/yr). Unsigned = right-click → Open on first launch (the sales page explains it). With `APPLE_ID`, `APPLE_PASSWORD` (app-specific), `APPLE_TEAM_ID` in the env, `npm run make` signs and notarizes automatically. Do this before running ads; the warning kills cold conversion.
- Windows and Linux builds need a Windows box or a GitHub Actions runner (upstream's workflow can be adapted). Intel Mac: `npm run make -- --arch=x64` on this machine.

## Gear Pro: how the money works

1. Buyer clicks the Stripe Payment Link on the sales page (`GEAR_PRO_MONTHLY_LINK` / `GEAR_PRO_YEARLY_LINK` in `gear.html`).
2. Stripe redirects to `https://gear.167-172-142-0.sslip.io/activate?session_id=...` which creates the key, shows it, and emails it (Resend). The webhook does the same if the page is never loaded.
3. The buyer clicks "Activate in Gear" (deep link `gear://gear-pro-return?key=...`) or pastes the key in Settings → AI Providers → Gear.
4. The app sends model requests to the gateway with that key; the gateway forwards to OpenRouter and meters `usage.cost` against the 30-day budget (`PRO_MONTHLY_BUDGET_USD`, default $20 = 300 credits in the app). Renewal invoices reset it; cancellation deactivates the key.
5. Manual keys (Build Sprint clients, friends): `curl -X POST https://gear.167-172-142-0.sslip.io/admin/keys -H "X-Admin-Token: ..." -d '{"email":"...","send_email":true}'`.

Pricing on the page: $39/mo or $390/yr, "founding price for the first 50 members, then it goes up". That line is a promise: raise the price after member 50.

Margin check: $39 revenue vs up to $20 of OpenRouter spend if the member maxes out, so worst case ~$19 gross per member per month. Most members will use far less. Change the budget in `server/.env` if it is too generous.

## Server (`server/`)

- `gateway.py` Flask app. Endpoints and env are documented at the top of the file and in `server/README.md`.
- `models.json` maps the app's model names (`dyad/auto`, `dyad/turbo`, ...) to OpenRouter ids. `dyad/auto` is what every Pro member gets by default.
- `deploy.sh` → droplet `/opt/gear-gateway`, systemd `gear-gateway`, nginx + certbot for `gear.167-172-142-0.sslip.io`. It copies the OpenRouter key from `/opt/slop-remake/.env` into a fresh `.env` on first deploy.
- `stripe_setup.py` creates the product, prices and payment links, and prints the webhook URL to register.

## Dan's checklist (in order)

1. **Deploy the gateway:** `cd ~/gear/server && ./deploy.sh`. Then on the droplet edit `/opt/gear-gateway/.env` (Stripe + Resend values) and `systemctl restart gear-gateway`. Check `https://gear.167-172-142-0.sslip.io/health`.
2. **Top up OpenRouter.** The shared key was out of credits on 2026-10-29. Pro does nothing without it.
3. **Stripe:** `cd ~/gear/server && STRIPE_SECRET_KEY=sk_live_... venv/bin/python stripe_setup.py`. Paste the two links into `~/danplants-web/gear.html`. Register the webhook it prints, put `STRIPE_WEBHOOK_SECRET` in the droplet `.env`, restart.
4. **Publish the app:** `gh auth login` if needed, then `./release.sh 1.0.0` (creates the public repo `wildmansignal/gear` if missing, pushes branch `gear` as `main`, uploads the DMG + ZIP).
5. **Ship the page:** in `~/danplants-web`, `git add gear.html api/gear-optin.js && git commit -m "Gear sales page" && git push` (Vercel deploys on push). Set Vercel env `RESEND_API_KEY`, `FROM_EMAIL`, `NOTIFY_EMAIL` if not already set, or the download email never sends (the page still shows the link).
6. **Apple Developer ID** before any paid traffic.
7. **GitHub integration inside Gear** needs Dan's own GitHub OAuth app (device flow enabled); put the client id in `src/brand.ts` (`githubClientId`) and wire it into `src/ipc/handlers/github_handlers.ts` (`GITHUB_CLIENT_ID`). Until then the GitHub connector in the app uses the upstream client id, which authorizes "Dyad" on the user's GitHub. Swap it before launch.

## What was deliberately left out

- Dyad's agent mode, turbo edits v2, visual editing, themes (all in Dyad's FSL-licensed `src/pro`, added after v0.26.0, not resellable).
- Dyad Pro's server-side smart context and lazy edits. The gateway injects the whole codebase like local mode does, so the Pro toggles for those features are gone.
- Neon integration (needed the upstream OAuth broker). Supabase works with a personal access token.
- Auto-update (needs signed builds; re-enable with `update-electron-app` once signing exists).
- Telemetry. Gone, not just off.

## The Forge

Gear is the tool inside The Forge, the $10,000 eight-week program (materials in `~/the-forge`, page danplants.com/forge). Each student gets a Gear Max key for 12 months: mint with `POST /admin/keys` using `{"email":..., "max_budget": 800, "note":"forge-<cohort>"}` and set `budget_reset_at` far out, or mint monthly.
