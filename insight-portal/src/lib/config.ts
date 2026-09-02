/**
 * Runtime configuration. Read from the environment on the server only.
 *
 * Nothing here is prefixed `NEXT_PUBLIC_`, so nothing here can reach the client bundle. The
 * semantic API URL, the gateway URL and the JWKS URL are all server-side facts; the browser talks
 * only to this application's own origin.
 */

function env(name: string, fallback: string): string {
  const value = process.env[name];
  return value === undefined || value === "" ? fallback : value;
}

export const config = {
  /** `semantic-api`, in-network. Contract 06 §1. */
  semanticApiUrl: env("INSIGHT_PORTAL_SEMANTIC_API_URL", "http://127.0.0.1:38200"),
  /** `login-gateway`, in-network. Contract 06 §1. */
  loginGatewayUrl: env("INSIGHT_PORTAL_LOGIN_GATEWAY_URL", "http://127.0.0.1:38120"),
  /**
   * The Odoo database the login form authenticates against. Configuration, not user input: the
   * database name is also the tenant slug, so leaving it on the form would put a tenant identifier
   * in a request body. The token the gateway returns remains the only authority on tenant anyway.
   */
  odooDatabase: env("INSIGHT_PORTAL_ODOO_DB", "bct"),
  /** JWKS for RS256 verification. Two keys are published; selection is by `kid`. Contract 06 §5. */
  jwksUrl: env(
    "INSIGHT_PORTAL_JWKS_URL",
    "http://127.0.0.1:38120/.well-known/jwks.json",
  ),
  jwtIssuer: env("INSIGHT_PORTAL_JWT_ISSUER", "https://login-gateway.local/"),
  jwtAudience: env("INSIGHT_PORTAL_JWT_AUDIENCE", "insight-portal"),
  sessionCookieName: env("INSIGHT_PORTAL_SESSION_COOKIE_NAME", "insight_portal_session"),
  refreshCookieName: env("INSIGHT_PORTAL_REFRESH_COOKIE_NAME", "insight_portal_refresh"),
  filtersCookieName: env("INSIGHT_PORTAL_FILTERS_COOKIE_NAME", "insight_portal_filters"),
  /** The gateway's own refresh cookie name, so logout can spend it upstream. */
  gatewayRefreshCookieName: env("INSIGHT_PORTAL_GATEWAY_REFRESH_COOKIE_NAME", "bct_refresh"),
  /** `false` only for plain-HTTP local development; the compose overlay leaves it at the default. */
  cookieSecure: env("INSIGHT_PORTAL_COOKIE_SECURE", "false") === "true",
  /** Upstream request timeout in ms. A hung warehouse must not hang the dashboard. */
  requestTimeoutMs: Number(env("INSIGHT_PORTAL_REQUEST_TIMEOUT_MS", "8000")),
  /** Server-side aggregate cache TTL ceiling, in seconds. Never exceeds the metric's own SLA. */
  cacheTtlCeilingSeconds: Number(env("INSIGHT_PORTAL_CACHE_TTL_SECONDS", "30")),
} as const;
