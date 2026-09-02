import "server-only";

import { Pool } from "pg";

/**
 * The site's only data access, and it reads two VIEWS rather than two tables.
 *
 * `marketing_site_reader` holds SELECT on `cms.published_page` and
 * `cms.published_block` and nothing else — not on `cms.page`. So "a draft is
 * never served" is a property of what this connection can reach, not of a
 * `WHERE is_published` that a future query might forget. There is no query
 * this file could be made to run that returns an unpublished row.
 *
 * The pool is small on purpose. This is a content site: every request is one
 * or two short reads, and the warehouse's connection budget is shared.
 */

const globalForPool = globalThis as unknown as { athera_cms_pool?: Pool };

function pool(): Pool {
  if (!globalForPool.athera_cms_pool) {
    const connectionString = process.env.MARKETING_SITE_DSN;
    if (!connectionString) {
      throw new Error("MARKETING_SITE_DSN is not set");
    }
    globalForPool.athera_cms_pool = new Pool({
      connectionString,
      max: Number(process.env.MARKETING_SITE_POOL_MAX ?? 4),
      connectionTimeoutMillis: 5000,
      idleTimeoutMillis: 30000,
    });
  }
  return globalForPool.athera_cms_pool;
}

export interface NavItem {
  slug: string;
  label: string;
}

export interface Block {
  kind: "hero" | "prose" | "feature_grid" | "cta" | "product";
  heading: string | null;
  body: string | null;
  data: Record<string, unknown>;
}

export interface Page {
  slug: string;
  title: string;
  summary: string | null;
  blocks: Block[];
}

export async function getNav(): Promise<NavItem[]> {
  const { rows } = await pool().query<{ slug: string; nav_label: string | null; title: string }>(
    `SELECT slug, nav_label, title
       FROM cms.published_page
      WHERE nav_order IS NOT NULL
      ORDER BY nav_order`,
  );
  return rows.map((r) => ({ slug: r.slug, label: r.nav_label ?? r.title }));
}

export async function getPage(slug: string): Promise<Page | null> {
  const client = await pool().connect();
  try {
    const page = await client.query<{ slug: string; title: string; summary: string | null }>(
      `SELECT slug, title, summary FROM cms.published_page WHERE slug = $1`,
      [slug],
    );
    if (page.rowCount === 0) return null;

    const blocks = await client.query<Block>(
      `SELECT kind, heading, body, data
         FROM cms.published_block
        WHERE slug = $1
        ORDER BY position`,
      [slug],
    );
    return { ...page.rows[0], blocks: blocks.rows };
  } finally {
    client.release();
  }
}

/** Liveness that actually touches the database, not just the process. */
export async function ping(): Promise<boolean> {
  try {
    await pool().query("SELECT 1");
    return true;
  } catch {
    return false;
  }
}
