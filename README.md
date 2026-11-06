# Gear

Build your own software with AI, on your own machine.

Gear is a desktop app (Mac, Windows, Linux) that turns a plain-English prompt into a working web app, then keeps editing it with you. The code lives on your computer. Bring your own AI keys, or use a Gear Pro key and skip the setup.

Sold at https://danplants.com/gear.

## Run it from source

```bash
npm ci
npm start
```

Requires Node 20 or newer.

## Build installers

```bash
npm run make
```

Output lands in `out/make/` (DMG + ZIP on Mac, Squirrel installer on Windows, deb/rpm on Linux). Mac builds are signed and notarized only when `APPLE_ID`, `APPLE_PASSWORD` and `APPLE_TEAM_ID` are set in the environment. See `GEAR.md` for the release checklist.

## Gear Pro gateway

`server/` holds the gateway that serves Gear Pro keys: an OpenAI-compatible proxy in front of OpenRouter with per-key monthly budgets, Stripe subscription webhooks and key delivery by email. See `server/README.md`.

## License

Gear is a modified version of [Dyad](https://github.com/dyad-sh/dyad) v0.26.0, used under the Apache License 2.0. See `LICENSE`, `NOTICE` and `CHANGES.md`.
