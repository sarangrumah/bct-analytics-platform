# custom_storefront_api

Headless JSON REST API that lets an external **Next.js storefront** (see
`/opt/odoo-platform/storefront`) drive Odoo as the e-commerce + FICO backend
for retail tenants (first consumer: **Gentle Woman**).

## Why
Odoo's built-in website/eCommerce is too rigid for the desired editorial
storefront. This module exposes a clean, CORS-enabled, JWT-secured API so the
frontend can own presentation while Odoo owns catalog, cart, orders, tax and
fulfilment.

## Auth model (three layers)
- **public + CORS** — `controllers/public_api.py`: categories, product list
  (PLP), product detail (PDP), shipping quote. No secrets, read-only.
- **JWT `auth="jwt_storefront"`** — `controllers/customer_api.py`: customer
  register/login/refresh/logout, profile, addresses, cart, checkout, payment,
  orders. Tokens minted via the vendored `auth_jwt` validator named
  `storefront` (HS256, `aud=storefront`, `iss=custom_storefront_api`); the
  customer partner is resolved from the `email` claim into
  `request.jwt_partner_id`. **Every query is scoped to that partner — the
  request body's partner id is never trusted.**
- **HMAC `@secure_endpoint("storefront")`** — `controllers/admin_api.py`:
  server-to-server only (the Next.js BFF). Verifies `X-Signature`/`X-Timestamp`
  = `HMAC-SHA256(secret, ascii(ts)+raw_body)` (see custom_core). Secret in
  `ir.config_parameter custom_core.secure_endpoint.storefront.secret`.

## Secrets (minted in `_post_init_storefront`)
- `auth.jwt.validator(storefront).secret_key` — JWT signing key (server-only).
- `custom_core.secure_endpoint.storefront.secret` — HMAC for the BFF; must be
  copied into the storefront container's `STOREFRONT_HMAC_SECRET`.
- `custom_storefront_api.access_ttl` — access token TTL (default 900s).
- `custom_storefront_api.cors_origin` — allowed browser origin (data file
  default `https://shop.gentle-woman.platform.localhost`; override per tenant).

## Reuse (no reinvention)
- **Cart** = website_sale draft `sale.order`; helpers in `models/sale_order.py`
  wrap `_cart_add` / `_cart_update_line_quantity` so pricelist/tax compute
  natively.
- **Shipping** = `delivery.carrier.id_rate_shipment(order)` from
  `custom_ecommerce`.
- **Payment** = `payment.transaction` + `custom_payment_id` adapters
  (POS adapter is stubbed until credentials arrive).

## Key endpoints
`GET /storefront/api/{categories,products,products/<id>}` (products accepts
`category`/`q`/`sort`/`page`/`limit` + `tag=<csv ids>` facet + `price_min`/`price_max`
range; response also returns `price_bounds {min,max}` over the non-price domain),
`GET /storefront/api/tags` (tags used on sellable products — PLP facets),
`GET /storefront/api/products/<id>/availability` (in-store stock per store),
`GET /storefront/api/content` (editorial CMS blocks keyed by code),
`GET /storefront/api/stores` (store locator),
`POST /storefront/api/shipping/quote`,
`POST /storefront/api/auth/{register,login,guest,refresh}`, `POST .../auth/logout`,
`GET .../customer/me`, `*/customer/addresses[/<id>]`,
`GET/POST/PUT/DELETE .../cart[...]`, `POST .../cart/shipping`,
`GET/POST .../wishlist`, `DELETE .../wishlist/<product_tmpl_id>`,
`POST .../wishlist/<product_tmpl_id>/move-to-cart`,
`POST .../wishlist/move-all-to-cart`,
`POST .../cart/pickup` (click & collect — set store warehouse),
`POST .../cart/address` (set delivery address inline — guest-friendly) (JWT, partner-scoped),
`POST .../checkout`, `POST .../checkout/<id>/pay`,
`GET .../orders[/<id>]`,
HMAC admin: `POST .../admin/{health,sync/products,orders/<id>/status}`.

## Store locator & product enrichment (spec F1/F5/F10)
- **Stores = published `stock.warehouse`** (Odoo is source of truth; no separate
  CMS). `models/stock_warehouse.py` adds `custom_storefront_published`,
  `custom_store_latitude/longitude`, `custom_store_hours`, `custom_store_image`
  + `_storefront_serialize()` (address pulled from `partner_id`). Admin edits via
  the Warehouse form (`views/storefront_views.xml`). **Seed store rows per tenant
  with a script — never a module data file** (would seed every tenant on upgrade).
- **Product detail** now also serializes `ref` (`default_code`), `tags`
  (`product.tag`), and `material` (`custom_material_composition`, Html/translatable
  on `product.template`). Variant-level `in_stock` already drives size availability.
- **Promo / strike-through price (spec F4):** `_storefront_serialize` resolves the
  active pricelist once (`_storefront_pricelist` = first `product.pricelist` by id;
  controllers pass it in to avoid a per-row search) and adds `price` (pricelist
  `_get_product_price`), `compare_at` (`list_price` when discounted), `discount_pct`.
  **Keep the pricelist currency == company currency (IDR)** or prices get FX-converted.
  Seed promo `product.pricelist.item` rows per tenant via script (not module data).
- **Tag facet filter:** `products?tag=<csv tag ids>` → `('product_tag_ids','in',[...])`.
- **Price range filter:** `products?price_min&price_max` filter on **`list_price`**
  (catalog price — deliberately matches the `price_asc/desc` sort, which also
  orders by list_price; a promo product is matched by its list price, not its
  discounted price). Response includes `price_bounds {min,max}` computed over the
  category/tag/search domain *excluding* the price filter (stable slider range).
  PLP UI = Min/Max number inputs + Apply/Reset.
- **Wishlist (spec F2):** model `custom.wishlist` (`models/wishlist.py`) =
  one row per `(partner_id, product_tmpl_id)` + optional `product_id` variant,
  `unique(partner_id, product_tmpl_id)`. ACL in `security/ir.model.access.csv`
  (system + portal; controllers still `sudo()`). Customer API (JWT, scoped to
  `request.jwt_partner_id` — body partner never trusted): `GET/POST /wishlist`
  (POST body `{product_id}` = template id, idempotent), `DELETE /wishlist/<tmpl_id>`.
  All three return the recomputed list; each item is the product card +
  `wishlist_id`. Frontend: `store/wishlist-store.ts` (hydrated site-wide from
  `Header` once a session exists), `WishlistButton` heart on cards/PDP,
  `/account/wishlist` page, Heart count in header. **Move-to-cart:**
  `POST /wishlist/<tmpl_id>/move-to-cart` adds the line (reusing
  `_storefront_add_line`) and unlinks the wishlist row in one request,
  returning `{cart, wishlist}`; the store applies both (`useCart.setCart` opens
  the drawer). "Move to cart" button per item on the wishlist page.
  **Move-all:** `POST /wishlist/move-all-to-cart` moves every *in-stock* row
  (out-of-stock left behind; per-item failures skipped), returns
  `{cart, wishlist, moved}`. The `move-all-to-cart` literal can't collide with
  `/wishlist/<int:tmpl_id>` (int converter only matches digits).
- **Auto "New" badge (spec F7):** `custom_drop_date` (Date) on `product.template` +
  computed non-stored `custom_is_new` = drop date (else `create_date`) within the
  "new" window (`ir.config_parameter custom_storefront_api.new_window_days`, default
  30; code-default fallback so no data file needed). Serialized as `is_new`. Drop
  dates are seeded per tenant by script — note demo products' `create_date` is
  recent, so set an OLD `custom_drop_date` to suppress the badge (drop date wins).

## In-store stock, consent, social (spec F11/F6/F8)
- **In-store stock (F11):** `product.template._storefront_store_availability()` returns
  per published warehouse `qty_available` **scoped via `with_context(location=
  wh.lot_stock_id.id)`** (the `warehouse=` context key did NOT scope — it summed all
  locations; use `location=`). Endpoint `GET /products/<id>/availability`. PDP shows
  "Tersedia di toko". Requires products `is_storable=True` + seeded `stock.quant` per
  store location (seed per tenant via script; `_update_available_quantity` rejects qty 0
  — skip zeros).
- **Click & Collect (F11):** `sale.order.custom_is_pickup` + native `warehouse_id`.
  `_storefront_set_pickup(warehouse_id)` validates a *published* store, drops the
  delivery line + carrier, sets `warehouse_id`+`custom_is_pickup`. `_storefront_apply_shipping`
  clears pickup and resets to `_storefront_default_warehouse()` (first non-published WH).
  `_storefront_checkout(..., pickup_warehouse_id=)` — pickup XOR carrier. Endpoint
  `POST /cart/pickup`; cart serialises `is_pickup`/`pickup_store_{id,name}`. Checkout page
  has a Dikirim/Ambil-di-toko toggle (store list from `/stores`).
- **UU PDP consent (F6):** `res.partner.custom_consent_marketing/_consent_data/
  _consent_date` (+ `custom_is_guest`). `_storefront_register` now requires
  `consent_data=True` (raises otherwise) and stamps the date; register endpoint &
  Next.js register form pass `consent_data`/`consent_marketing`. Cookie banner =
  `components/layout/CookieConsent.tsx` (localStorage `gw-cookie-consent`, default
  privacy-preserving) in the root layout.
- **Social & share (F8):** Footer social links (Instagram/TikTok/Facebook/WhatsApp,
  overridable via `NEXT_PUBLIC_SOCIAL_*`); PDP "Share" button uses the Web Share API
  with a clipboard fallback.

## Editorial content / CMS (spec F9 — Odoo-backed, NOT Payload)
Non-catalog editorial content (hero banner, section images, headlines/CTA copy) lives in
**`custom.storefront.content`** (`models/storefront_content.py`) — one row per block keyed by
a stable `code` (`hero`/`editorial`/`lookbook`/`editorial_2`/`featured`/`plp_promo`/`footer`/
`announcement` so far — add more freely; `announcement` = dismissible header bar, its visibility
drives `store/ui-store.ts` which offsets the fixed Header `top` + `MainShell` padding; the home
**hero stays the 3D `HeroCanvas`** — CMS supplies only its copy, NOT a background image):
translatable `eyebrow/headline/body/cta_label`
(+ `cta_url`, `image` Binary). Edited in Odoo **Storefront ▸ Site Content** (no separate CMS
service — the minimal-infra choice over Payload). `GET /storefront/api/content?lang=` returns a
`{code: block}` dict; `image` = `/web/image/custom.storefront.content/<id>/image`.
**ACL gotcha:** the image is served by `/web/image` (NOT sudo) to anonymous visitors, so the
model needs **`base.group_public` read** (+ portal) or `/web/image` returns the grey placeholder.
Frontend `components/home/HomeSections.tsx` fetches once and renders hero (3D `HeroCanvas` + CMS
copy), editorial split, lookbook banner, second reversed editorial, **collections trio**
(`collection_1/2/3` image tiles), featured heading, and **newsletter**; re-fetches on locale change.
Seed blocks per tenant via script (reuse product `image_1920` for demo banners).
**Newsletter signup:** `custom.storefront.subscriber` (email unique) + public `POST
/storefront/api/newsletter` (`_storefront_subscribe`, idempotent, email-regex validated) →
`components/home/Newsletter.tsx`. Subscribers list in Odoo **Storefront ▸ Newsletter Subscribers**.

## Multilanguage ID/EN (spec F3)
- Public catalog endpoints accept `?lang=id|en` → mapped to `id_ID`/`en_US` and applied
  via `_model(name, kw)` = `request.env[name].sudo().with_context(lang=...)`, so all
  translatable fields (`name`, `description_sale`, `custom_material_composition`, category
  & tag names) render in that language. Activate `id_ID` per tenant (`res.lang._activate_lang`).
- **`custom_material_composition` is `fields.Text(translate=True)`** (model-translation =
  independent per-language whole value, like `name`). It was briefly `fields.Html` — DON'T:
  `Html(translate=True)` is **term-based `xml_translate`**, where writing a full value in a
  non-source lang clobbers the other languages. Set translations with
  `record.update_field_translations('field', {'en_US': ..., 'id_ID': ...})`.
- Frontend: `store/locale-store.ts` (zustand persist `gw-locale`) + `lib/i18n.ts` dict;
  `lib/client.ts` sends `?lang=` from `useLocale.getState().locale`; pages include `locale`
  in fetch deps so content re-fetches on switch. Catalog content switches via Odoo; UI chrome
  via dict.
- **URL routing `/id`·`/en`** (storefront `src/middleware.ts`, next-intl-style without moving
  the route tree): prefixed paths are **rewritten** to the unprefixed route + set `NEXT_LOCALE`
  cookie + forward an `x-locale` request header; unprefixed paths **redirect** to
  `/{cookie|default}{path}`. `components/LocaleSync.tsx` syncs the store + `<html lang>` from the
  URL; Header switcher navigates to the other locale prefix. Server `generateMetadata` reads
  `x-locale` (via `next/headers`) so OG/Twitter cards are localised per URL. `matcher` excludes
  `/api`, `_next`, files.

## Guest checkout (spec F2)
`POST /storefront/api/auth/guest {email, name, consent_data}` mints an **access-only**
(no refresh) JWT for a password-less guest partner (`res.partner._storefront_guest`,
`custom_is_guest=True`). Because the validator resolves the partner from the email claim
and **requires exactly one match**, the helper: rejects emails that belong to a registered
`res.users` ("please sign in"), refuses ambiguous (>1 partner) emails, and otherwise
reuses/creates a single guest partner. The whole JWT cart/checkout machinery then works
unchanged. UU PDP `consent_data` is mandatory. Frontend: a "Checkout sebagai tamu" form on
`/account/login` → `guestLogin()` → session → `/checkout`.
**Guest shipping address:** `sale_order._storefront_set_shipping_address(addr)` find-or-updates
a single `type='delivery'` child of the cart partner (country defaults to Indonesia `base.id`)
and sets `partner_shipping_id`. Exposed as `POST /cart/address` and accepted inline by
`_storefront_checkout(shipping_address=)` (set BEFORE rating the carrier so `id_rate_shipment`
sees the real zip). Cart serialises `shipping_address`. **Saved addresses (members):** checkout loads
`GET /customer/me` addresses, shows a radio selector + "+ Tambah alamat baru" (saves via
`POST /customer/addresses`); selecting one passes `shipping_address_id`. Guests (no refresh
token → `isGuest`) just get the inline form. **IDOR guard:** `_storefront_owned_address`
asserts any `shipping_address_id`/`billing_address_id` belongs to the cart's commercial partner
(else "not yours") — the body's address id is never trusted. Pickup mode needs no address.

## Accounts as Odoo customers + address book (delivery + billing)
- Registration sets **`customer_rank=1`** so the account shows under Odoo **Sales ▸ Customers**
  (guests already get rank 1). Every storefront account is a real `res.partner` + portal
  `res.users`. (Existing accounts were back-filled to rank 1 once.)
- **Address book:** `/account/addresses` (Next.js) manages child addresses of any
  `type` — **Pengiriman (`delivery`)** + **Penagihan (`invoice`)** — via the existing
  `GET /customer/me` + `POST/PUT/DELETE /customer/addresses[/<id>]` endpoints
  (create/edit/delete). Checkout loads them: a shipping selector (delivery) and a
  "Alamat penagihan sama dengan pengiriman" toggle that, when off, picks a saved
  **invoice** address → `billing_address_id` (ownership-guarded by `_storefront_owned_address`).
  Order ends with distinct `partner_shipping_id` / `partner_invoice_id`.

## Security hardening (storefront)
- **SQLi:** none — all DB access is via the Odoo ORM (parameterised); no raw
  `cr.execute` in the custom modules.
- **XSS:** the only `dangerouslySetInnerHTML` (product description) is run through
  `lib/sanitize.ts` (`isomorphic-dompurify`, tight tag/attr allowlist); everything
  else is React-escaped. Backed by CSP.
- **CSP + headers** (`src/middleware.ts`, all page responses): `default-src 'self'`,
  `object-src/frame-src/frame-ancestors 'none'`, `img 'self' data: blob:`,
  `connect/form-action/base-uri 'self'`, `script-src 'self' 'unsafe-inline'`
  (a per-request nonce is **incompatible with Next static prerendering**, so inline
  is allowed but ALL external/`src` scripts are blocked), `style-src 'self'
  'unsafe-inline'` (React inline styles), `upgrade-insecure-requests`. Plus
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  `Permissions-Policy` (geolocation=self only), HSTS, COOP.
- **SSRF:** `/api/img/[...path]` is locked to `^web/image/` (no `..`) and only relays
  `image/*` responses — it can no longer proxy arbitrary Odoo paths.
- **Rate limiting + body cap** (`/api/store` BFF, `lib/ratelimit.ts`): auth
  login/register/guest 8/min, newsletter 5/min, refresh 30/min per IP → 429; request
  bodies > 64 KB → 413. (In-memory/single-instance; back with Redis when scaling.)
- **Address IDOR:** `_storefront_owned_address` validates any
  shipping/billing address id belongs to the cart's commercial partner.
- **Input caps:** address free-text capped server-side (defence-in-depth).
- **Auth tokens = HttpOnly cookies (done):** the BFF auth routes
  (`app/api/store/auth/{login,register,guest,logout}`) move Odoo's access/refresh tokens
  into `gw_at`/`gw_rt` cookies (`HttpOnly; Secure; SameSite=Lax`) and return only the
  `customer` object — tokens are **never** in JS/localStorage, so XSS can't steal a session.
  The `/api/store` proxy injects the Bearer from `gw_at` server-side and, on a 401,
  **transparently refreshes** via `gw_rt` (re-issuing both cookies) and retries once.
  `lib/session.ts` holds the cookie + `authProxy` helpers; `auth-store.ts` keeps only
  `{customer, isGuest}`; all `lib/client.ts` calls dropped their token args.
- **Internal hop TLS (done):** BFF→Odoo now rides a **dedicated TLS sidecar** `storefront-tls`
  (compose service: `nginx:alpine` + `nginx/storefront-tls.conf`, pure TLS→`odoo:8069`, NONE of
  the shared nginx's Odoo-web tuning which 500/405'd storefront POSTs). `lib/odoo.ts` builds an
  undici `Agent` that **pins** the mounted cert (`/etc/ssl/odoo-internal.crt`) and skips hostname
  (cert CN=localhost); active when `ODOO_BASE_URL` is https (`STOREFRONT_ODOO_BASE_URL=https://storefront-tls`
  in `.env`). Proven: without the pin the hop fails `DEPTH_ZERO_SELF_SIGNED_CERT`. Shared nginx/cert
  untouched → other tenants unaffected.
- **Token sealed at rest (done):** the cookie stores the JWT **AES-256-GCM sealed** (`sealToken`/
  `openToken` in `lib/session.ts`, key = sha256 of `STOREFRONT_COOKIE_KEY||STOREFRONT_HMAC_SECRET`),
  so the email claim isn't readable on the user's disk. The `/api/store` proxy + logout `openToken`
  before injecting `Bearer`; `openToken` fails closed (→ logged out, not 500). Odoo still gets a
  normal HS256 JWT (no auth_jwt/JWE change).
- **At-rest PII encryption (done, gentlewoman-only):** `res.partner` `phone/street/street2/zip`
  are now **non-stored computed** fields backed by Fernet-encrypted `custom_*_enc` columns
  (`ENC::` envelope), via reusable `custom.ir.config.encrypt_value/decrypt_value` (added to
  `custom_core`, master key `CORETAX_SERTEL_MASTER_KEY`). `email`/`name` stay plaintext
  (load-bearing for login/JWT/uniqueness). Reads decrypt transparently (serializers, /customer/me,
  checkout, back-office form); **writes encrypt via the field inverse**. **Trade-off:** these
  fields are no longer searchable/groupable in the Odoo back-office. Migrated existing rows once
  (snapshot → encrypt → null plaintext columns), DB backup kept. Other tenants lack the override
  (no `custom_*_enc` columns) → unaffected.
- **Browser↔BFF payload encryption: intentionally NOT done** (security theater over TLS+HttpOnly;
  key would live in client JS, doesn't stop XSS/MITM — would only add attack surface).
- **Residual / TODO:** CORS `cors_origin` can be pinned per tenant (browser never hits Odoo
  directly — all traffic is same-origin via the BFF); rate-limiter is in-memory (use Redis
  when multi-instance). The pre-encryption DB rollback dump under `data/odoo-filestore/` still
  holds plaintext — secure/delete it after a stability window.

## Caveats / production hardening
- The JWT validator's `static_user_id` is admin; controllers use `sudo()` and
  strict partner scoping. For tighter least-privilege, point it at a dedicated
  portal service user.
- Refresh tokens are stored hashed in `custom.storefront.token`; the storefront
  scaffold keeps them in localStorage — move to a BFF-set HttpOnly cookie for
  production.
