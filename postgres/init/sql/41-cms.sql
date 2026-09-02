-- ============================================================================
-- cms — the content behind the public ATHERA site.
--
-- The diagram draws ONE Postgres under the left branch, feeding both Super
-- Admin CMS and the public pages. This is the second half of that database:
-- tenant_registry answers "who are our clients", and cms answers "what does
-- the website say". Same database, because they are edited by the same people
-- from the same console and a second cluster would buy nothing.
--
-- WHY THE SITE IS NOT ODOO'S WEBSITE MODULE. Odoo's website is installed and
-- would render pages perfectly well, but its content lives in the TENANT
-- database. The public site belongs to ATHERA, not to any client, and putting
-- it in a tenant would mean the marketing pages disappear when that tenant is
-- suspended.
--
-- ONE WRITER, MANY READERS. tenant_orchestrator writes (the console edits
-- through it). marketing_site_reader has SELECT on published rows and nothing
-- else -- the public site cannot see a draft even if it asked, because the
-- view it reads through does not contain one.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS cms;
COMMENT ON SCHEMA cms IS 'Content for the public ATHERA site';

-- ============================================================================
-- Pages. The diagram's "Company Profile / Product / ETC." are rows here, not
-- routes in code -- adding a page is an edit, not a deploy.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cms.page (
  id            BIGSERIAL PRIMARY KEY,
  -- The URL path, without a leading slash. '' is the home page.
  slug          VARCHAR(120) NOT NULL UNIQUE
                CHECK (slug ~ '^[a-z0-9]*(-[a-z0-9]+)*(/[a-z0-9]+(-[a-z0-9]+)*)*$'),
  title         VARCHAR(200) NOT NULL,
  summary       TEXT,
  -- Ordering in the navigation. NULL means "not in the nav" — a page can be
  -- published and reachable by URL without appearing in the menu.
  nav_order     INTEGER,
  nav_label     VARCHAR(60),
  is_published  BOOLEAN NOT NULL DEFAULT false,
  published_at  TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_by    VARCHAR(128)
);

CREATE INDEX IF NOT EXISTS page_nav_idx ON cms.page(nav_order)
  WHERE is_published AND nav_order IS NOT NULL;

-- ============================================================================
-- Blocks. A page is an ordered list of typed blocks rather than a blob of
-- HTML, because HTML from a database is an XSS hole the moment one editor
-- pastes something from a word processor. The renderer knows a fixed set of
-- block types and escapes every field; a `kind` it does not recognise is
-- skipped rather than guessed at.
-- ============================================================================
CREATE TABLE IF NOT EXISTS cms.block (
  id        BIGSERIAL PRIMARY KEY,
  page_id   BIGINT NOT NULL REFERENCES cms.page(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL DEFAULT 0,
  kind      VARCHAR(24) NOT NULL
            CHECK (kind IN ('hero', 'prose', 'feature_grid', 'cta', 'product')),
  heading   VARCHAR(200),
  body      TEXT,
  -- Typed payload per kind: feature_grid takes {items:[{title,body}]},
  -- cta takes {label,href}, product takes {name,tagline,points:[]}.
  data      JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (page_id, position)
);

CREATE INDEX IF NOT EXISTS block_page_idx ON cms.block(page_id, position);

-- ============================================================================
-- The read surface. The site reads THIS, never the tables.
--
-- A view rather than a permission on the table, so "the public site cannot
-- serve a draft" is a property of what it can see rather than of a WHERE
-- clause someone might forget. There is no way to ask this view for an
-- unpublished page.
-- ============================================================================
CREATE OR REPLACE VIEW cms.published_page AS
SELECT p.id, p.slug, p.title, p.summary, p.nav_order, p.nav_label, p.updated_at
  FROM cms.page p
 WHERE p.is_published;

CREATE OR REPLACE VIEW cms.published_block AS
SELECT b.id, b.page_id, p.slug, b.position, b.kind, b.heading, b.body, b.data
  FROM cms.block b
  JOIN cms.page p ON p.id = b.page_id
 WHERE p.is_published;

-- ============================================================================
-- Seed. Enough for the site to be a site on first boot rather than a 404 —
-- the diagram's three named pages, plus the three products.
-- ============================================================================
INSERT INTO cms.page (slug, title, summary, nav_order, nav_label, is_published, published_at)
VALUES
  ('',        'ATHERA', 'Platform bisnis untuk perusahaan yang tumbuh.', 1, 'Beranda', true, now()),
  ('profil',  'Profil Perusahaan', 'Siapa kami dan bagaimana kami bekerja.', 2, 'Profil', true, now()),
  ('produk',  'Produk', 'Tiga produk, satu platform.', 3, 'Produk', true, now()),
  ('kontak',  'Hubungi Kami', 'Ceritakan kebutuhan Anda.', 4, 'Kontak', true, now())
ON CONFLICT (slug) DO NOTHING;

INSERT INTO cms.block (page_id, position, kind, heading, body, data)
SELECT p.id, v.position, v.kind, v.heading, v.body, v.data::jsonb
  FROM (VALUES
    ('',       0, 'hero',         'Satu platform, tiga produk',
     'ATHERA menyatukan dasbor, ERP, dan asisten AI di atas data Anda sendiri.', '{}'),
    ('',       1, 'feature_grid', 'Apa yang kami kerjakan', NULL,
     '{"items":[{"title":"ATHERA Insight","body":"Dasbor yang dibangun dari database Anda."},{"title":"Odoo","body":"Implementasi, modul khusus, dan Odoo Care."},{"title":"ATHERA Agent","body":"Asisten yang menjawab dari data Anda, dan hanya itu."}]}'),
    ('',       2, 'cta',          NULL, NULL, '{"label":"Hubungi kami","href":"/kontak"}'),
    ('profil', 0, 'prose',        'Tentang ATHERA',
     'Kami membangun dan merawat sistem bisnis: dasbor yang bisa dipercaya, ERP yang benar-benar dipakai, dan otomatisasi yang tidak menambah pekerjaan.', '{}'),
    ('produk', 0, 'product',      NULL, NULL,
     '{"name":"ATHERA Insight","tagline":"Dasbor dari database Anda.","points":["Terhubung ke Postgres milik Anda","Isolasi antar klien di tingkat penyimpanan","Angka yang bisa direkonsiliasi ke sumbernya"]}'),
    ('produk', 1, 'product',      NULL, NULL,
     '{"name":"Odoo","tagline":"Implementasi, modul khusus, Odoo Care.","points":["Implementasi dari intake sampai handover","Modul khusus sesuai proses Anda","Perawatan berkelanjutan"]}'),
    ('produk', 2, 'product',      NULL, NULL,
     '{"name":"ATHERA Agent","tagline":"Bertanya dengan bahasa biasa.","points":["Menjawab dari data Anda sendiri","Tidak pernah menulis SQL","Tunduk pada hak akses pengguna"]}'),
    ('kontak', 0, 'prose',        'Hubungi Kami',
     'Ceritakan kebutuhan Anda dan kami akan menghubungi kembali.', '{}')
  ) AS v(slug, position, kind, heading, body, data)
  JOIN cms.page p ON p.slug = v.slug
ON CONFLICT (page_id, position) DO NOTHING;
