/**
 * Gear ships without analytics. The UI still calls the PostHog-style hooks
 * inherited from Dyad, so these no-op stand-ins keep the call sites intact
 * while guaranteeing nothing leaves the user's machine.
 */
type AnyArgs = unknown[];
const noopClient = {
  capture: (..._args: AnyArgs): void => {},
  captureException: (..._args: AnyArgs): void => {},
  identify: (..._args: AnyArgs): void => {},
};
export type TelemetryClient = typeof noopClient;
export function usePostHog(): TelemetryClient {
  return noopClient;
}
export const posthog = noopClient;
