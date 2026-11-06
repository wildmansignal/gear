/**
 * Gear brand configuration. Every user-facing name, link and service URL
 * lives here so the product can be renamed or re-pointed in one place.
 */
export const BRAND = {
  name: "Gear",
  proName: "Gear Pro",
  tagline: "Build your own gear. No permission.",
  /** Custom URL scheme used for deep links (gear://...). */
  protocol: "gear",
  siteUrl: "https://danplants.com/gear",
  proUrl: "https://danplants.com/gear#pro",
  helpUrl: "https://danplants.com/gear#help",
  supportEmail: "dan@danplants.com",
  /** Default folder name (under the home directory) where apps are created. */
  appsFolder: "gear-apps",
  /**
   * The Gear gateway: an OpenAI-compatible proxy that serves Gear Pro keys.
   * Override at build/run time with GEAR_GATEWAY_URL (must include /v1).
   */
  gatewayUrl: "https://gear.167-172-142-0.sslip.io",
  /** GitHub OAuth app (device flow) used by the GitHub integration. */
  githubClientId: "",
} as const;

export function gatewayBase(): string {
  const env =
    typeof process !== "undefined" ? process.env?.GEAR_GATEWAY_URL : undefined;
  if (env) return env.replace(/\/v1\/?$/, "").replace(/\/$/, "");
  return BRAND.gatewayUrl;
}
