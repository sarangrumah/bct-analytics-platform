import { notFound } from "next/navigation";

import { getPage, type Block } from "@/lib/cms";

export const dynamic = "force-dynamic";

/**
 * Every public page, including the home page, from one route.
 *
 * The diagram's "Company Profile / Product / ETC." are ROWS, not routes — a
 * new page is an edit in the console, not a deploy. `[...slug]` with an
 * optional catch-all is what lets `/` and `/profil/apa-pun` come from the same
 * handler.
 *
 * BLOCKS ARE TYPED, AND NOTHING IS RENDERED AS HTML. Every field goes through
 * JSX, which escapes it. There is no `dangerouslySetInnerHTML` anywhere in
 * this file, and there must not be: the moment one editor pastes markup out of
 * a word processor, an HTML column becomes stored XSS on the company's own
 * front page. A block `kind` this renderer does not know is SKIPPED rather
 * than guessed at, so adding a type to the database cannot render garbage
 * before the code that understands it ships.
 */

function asItems(data: Record<string, unknown>): { title: string; body: string }[] {
  const items = (data as { items?: unknown }).items;
  if (!Array.isArray(items)) return [];
  return items.flatMap((i) =>
    i && typeof i === "object" && typeof (i as { title?: unknown }).title === "string"
      ? [{ title: String((i as { title: string }).title), body: String((i as { body?: unknown }).body ?? "") }]
      : [],
  );
}

function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function Render({ block }: { block: Block }) {
  const d = block.data ?? {};
  switch (block.kind) {
    case "hero":
      return (
        <section>
          <h1>{block.heading}</h1>
          {block.body ? <p className="lede">{block.body}</p> : null}
        </section>
      );
    case "prose":
      return (
        <section>
          {block.heading ? <h2>{block.heading}</h2> : null}
          {block.body ? <p>{block.body}</p> : null}
        </section>
      );
    case "feature_grid":
      return (
        <section>
          {block.heading ? <h2>{block.heading}</h2> : null}
          <div className="grid">
            {asItems(d).map((item) => (
              <div className="card" key={item.title}>
                <h3>{item.title}</h3>
                <p>{item.body}</p>
              </div>
            ))}
          </div>
        </section>
      );
    case "product": {
      const name = String((d as { name?: unknown }).name ?? "");
      return (
        <section className="product">
          <h3>{name}</h3>
          <p className="tag">{String((d as { tagline?: unknown }).tagline ?? "")}</p>
          <ul>
            {asStrings((d as { points?: unknown }).points).map((p) => <li key={p}>{p}</li>)}
          </ul>
        </section>
      );
    }
    case "cta": {
      const label = String((d as { label?: unknown }).label ?? "");
      const href = String((d as { href?: unknown }).href ?? "");
      // Only a same-site path is ever followed. An href out of the database
      // that could name any origin turns the company's own front page into an
      // open redirect, which is a phishing primitive.
      if (!label || !href.startsWith("/")) return null;
      return <section className="cta"><a href={href}>{label}</a></section>;
    }
    default:
      // Unknown kind: render nothing. See the file docstring.
      return null;
  }
}

export default async function Page({ params }: { params: Promise<{ slug?: string[] }> }) {
  const { slug } = await params;
  const path = (slug ?? []).join("/");
  const page = await getPage(path);
  if (page === null) notFound();

  return (
    <>
      {path !== "" && page.blocks[0]?.kind !== "hero" ? (
        <>
          <h1>{page.title}</h1>
          {page.summary ? <p className="lede">{page.summary}</p> : null}
        </>
      ) : null}
      {page.blocks.map((block, i) => <Render key={i} block={block} />)}
    </>
  );
}
