# Changes from Dyad v0.26.0

Gear forks Dyad at tag v0.26.0 (2025-10-24), the last release licensed entirely under Apache 2.0. Later Dyad releases add a `src/pro` directory under the Functional Source License, which does not permit resale, so nothing from those releases is included.

- Product renamed to Gear; `gear://` URL scheme; apps created under `~/gear-apps`.
- New logo and icons.
- PostHog analytics removed. No usage data leaves the machine.
- Auto-updater removed (it pointed at upstream releases). New versions are downloaded from the Gear site.
- Dyad Pro replaced by Gear Pro: the "auto" provider talks to the Gear gateway (`src/brand.ts`, overridable with `GEAR_GATEWAY_URL`).
- Supabase connects with a personal access token instead of the upstream OAuth broker.
- Neon integration hidden (it depended on the upstream OAuth broker).
- Help bot, community templates and log upload point at the Gear gateway.
- Upstream docs, pricing and community links replaced with Gear links.
- "Made with Dyad" badge in generated apps is now "Built with Gear".
- Packaging: DMG maker added, signing only when Apple credentials exist, GitHub publisher points at wildmansignal/gear.
