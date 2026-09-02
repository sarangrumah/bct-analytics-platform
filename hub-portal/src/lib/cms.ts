import "server-only";

import { Pool } from "pg";

/**
 * The console's half of the CMS: it can see drafts, because editing them is
 * the job. The public site reads the same tables through two views under a
 * role with no grant on them at all — see marketing-site/src/lib/cms.ts.
 */

const globalForPool = globalThis as unknown as { hub_cms_pool?: Pool };

function pool(): Pool {
  if (!globalForPool.hub_cms_pool) {
    const connectionString = process.env.HUB_PORTAL_CMS_DSN;
    if (!connectionString) throw new Error("HUB_PORTAL_CMS_DSN is not set");
    globalForPool.hub_cms_pool = new Pool({
      connectionString,
      max: Number(process.env.HUB_PORTAL_POOL_MAX ?? 4),
      connectionTimeoutMillis: 5000,
    });
  }
  return globalForPool.hub_cms_pool;
}

export interface CmsPage {
  id: number;
  slug: string;
  title: string;
  nav_order: number | null;
  is_published: boolean;
  updated_at: string;
  blocks: number;
}

export async function listPages(): Promise<CmsPage[]> {
  const { rows } = await pool().query<CmsPage>(
    `SELECT p.id, p.slug, p.title, p.nav_order, p.is_published, p.updated_at,
            (SELECT count(*)::int FROM cms.block b WHERE b.page_id = p.id) AS blocks
       FROM cms.page p
      ORDER BY COALESCE(p.nav_order, 9999), p.slug`,
  );
  return rows;
}

export async function setPublished(id: number, published: boolean, actor: string): Promise<void> {
  await pool().query(
    `UPDATE cms.page
        SET is_published = $2,
            published_at = CASE WHEN $2 THEN COALESCE(published_at, now()) ELSE published_at END,
            updated_at = now(), updated_by = $3
      WHERE id = $1`,
    [id, published, actor],
  );
}
