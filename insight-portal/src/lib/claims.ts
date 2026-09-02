import type { JWTPayload } from "jose";

/**
 * The claim set from contract 02, and the mapping from a verified payload onto it.
 *
 * Deliberately in its own file with no runtime imports, so the entitlement rules can be tested
 * directly by `tests/claims.test.ts` rather than through a mock of the verifier. The rule that
 * matters most here was a real privilege escalation earlier in this build, and a rule of that kind
 * should be exercised by a test that names it.
 */
export interface Session {
  iss: string;
  aud: string;
  sub: string;
  tenant_id: string;
  odoo_uid: number;
  roles: string[];
  /** Operating Unit ids. **An empty array means NO Operating Units, never "all".** */
  allowed_ou: number[];
  /** The explicit bypass. **Absent is `false`.** Never infer it from an empty `allowed_ou`. */
  all_ou: boolean;
  company_ids: number[];
  /**
   * The diagram's "Super Admin?" decision. **Absent is `false`.** Derived in the gateway from
   * `custom_super_admin.group_super_admin` and nowhere else, so a session cannot claim it by
   * having a role name that merely looks administrative.
   */
  is_super_admin: boolean;
  /**
   * The diagram's "Active?" decision, answered by `tenant_registry.is_active()` in the control
   * plane. **Absent is `false`**, which is the same rule `all_ou` follows and for the same reason:
   * a token minted before this claim existed must not be read as a paid subscription.
   */
  subscription_active: boolean;
  /** Which ATHERA products the tenant's plan grants. Empty means none, never all. */
  products: string[];
  iat: number;
  exp: number;
}

function asNumberArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is number => typeof entry === "number");
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string");
}

/**
 * Map a verified payload onto `Session`.
 *
 * `payload.all_ou === true` is the whole point of the GATE 3 amendment: an ABSENT claim becomes
 * `false`, so a token that predates the claim grants nothing rather than everything, and an empty
 * `allowed_ou` is never read as a bypass. The two documents disagreeing about what `[]` meant is
 * what produced a user who would have seen more in the dashboard than in Odoo, with nothing
 * reporting it, because every row returned was genuinely in the right tenant.
 *
 * Note what this does NOT do: it does not fall back to a default tenant, and it returns `null`
 * rather than an empty string when `tenant_id` is missing. A session with no tenant is not a
 * session with a blank tenant.
 */
export function toSession(payload: JWTPayload): Session | null {
  const tenant = payload.tenant_id;
  if (typeof tenant !== "string" || tenant === "") return null;
  if (typeof payload.sub !== "string") return null;
  const aud = payload.aud;
  return {
    iss: typeof payload.iss === "string" ? payload.iss : "",
    aud: typeof aud === "string" ? aud : Array.isArray(aud) ? (aud[0] ?? "") : "",
    sub: payload.sub,
    tenant_id: tenant,
    odoo_uid: typeof payload.odoo_uid === "number" ? payload.odoo_uid : -1,
    roles: asStringArray(payload.roles),
    allowed_ou: asNumberArray(payload.allowed_ou),
    all_ou: payload.all_ou === true,
    company_ids: asNumberArray(payload.company_ids),
    // `=== true`, not truthiness, and not `!== false`. Same shape as all_ou above: an absent or
    // malformed claim becomes false, so an old token grants nothing rather than everything.
    is_super_admin: payload.is_super_admin === true,
    subscription_active: payload.subscription_active === true,
    products: asStringArray(payload.products),
    iat: typeof payload.iat === "number" ? payload.iat : 0,
    exp: typeof payload.exp === "number" ? payload.exp : 0,
  };
}
