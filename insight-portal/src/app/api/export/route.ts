import { NextResponse, type NextRequest } from "next/server";

import { dimensionLabel, formatDimension } from "@/lib/format";
import { decodePanelQuery } from "@/lib/panel";
import { query } from "@/lib/semantic";
import { buildCsv, buildXlsx, type Cell } from "@/lib/xlsx";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * CSV and XLSX export.
 *
 * The masking rule in the brief is satisfied structurally rather than by a code path that
 * remembers to mask. This handler calls `query()` - the same function the pages call, the only
 * function in this application that obtains data - and writes out the rows it returns. Those rows
 * were masked upstream, in the warehouse, per contract 01. `semantic-api` performs no masking and
 * can perform none: there is no salt in that process and no unmasking function anywhere in the
 * codebase. This application holds no database credential of any kind, so there is no second path
 * an export could take even if one were wanted. `tests/export-no-unmask.test.mjs` asserts the
 * absence rather than the intent: no database driver in the dependency tree, and no symbol in the
 * source that would decrypt, unhash or reverse a masked value.
 *
 * The tenant is resolved inside `query()` from the verified session, so the encoded query string
 * cannot be edited into somebody else's data. It can be edited into a different declared metric,
 * which the session was already entitled to read.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const params = request.nextUrl.searchParams;
  const encoded = params.get("q");
  const format = params.get("format") === "xlsx" ? "xlsx" : "csv";
  const requestedName = params.get("name") ?? "export";
  const filename = requestedName.replace(/[^A-Za-z0-9._-]/g, "-").slice(0, 60) || "export";

  if (encoded === null) {
    return NextResponse.json(
      { error: "invalid_query", detail: "Missing q parameter.", field: "q" },
      { status: 400 },
    );
  }

  const spec = decodePanelQuery(encoded);
  if (spec === null) {
    return NextResponse.json(
      { error: "invalid_query", detail: "Unreadable export query.", field: "q" },
      { status: 400 },
    );
  }

  const result = await query(spec);
  if (!result.ok) {
    return NextResponse.json(result.body, { status: result.status });
  }

  const data = result.data;
  const header: Cell[] = [
    ...data.dimensions.map((dimension) => dimensionLabel(dimension)),
    data.meta.unit === null ? "Nilai" : "Nilai (" + data.meta.unit + ")",
  ];
  const body: Cell[][] = data.rows.map((row) => [
    ...data.dimensions.map((dimension) => formatDimension(dimension, row[dimension] ?? null)),
    row.value,
  ]);

  /**
   * A provenance footer. An exported file outlives the screen it came from, so it carries the
   * metric, the source model, the pipeline timestamp and whether the warehouse considered the
   * figure stale when it was taken. A spreadsheet with no provenance is how a stale number gets a
   * second life in a meeting three weeks later.
   */
  const footer: Cell[][] = [
    [],
    ["Metrik", data.metric],
    ["Model sumber", data.meta.source_model],
    ["Tenant", data.meta.tenant_id],
    ["Diperbarui (pipeline)", data.meta.last_refreshed_at],
    ["Status kesegaran", data.meta.is_stale ? "BASI" : "segar"],
    ["SLA (detik)", data.meta.refresh_sla_seconds],
    ["Jumlah baris", data.meta.row_count],
  ];

  const rows: Cell[][] = [header, ...body, ...footer];

  if (format === "xlsx") {
    const workbook = buildXlsx(data.metric.slice(0, 31), rows);
    return new NextResponse(workbook as unknown as BodyInit, {
      status: 200,
      headers: {
        "content-type":
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "content-disposition": 'attachment; filename="' + filename + '.xlsx"',
        "cache-control": "no-store",
      },
    });
  }

  // A UTF-8 BOM, because Excel on Windows reads a BOM-less CSV as the system codepage and turns
  // every Indonesian label into mojibake. The operator opens these on Windows.
  const csv = "﻿" + buildCsv(rows);
  return new NextResponse(csv, {
    status: 200,
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": 'attachment; filename="' + filename + '.csv"',
      "cache-control": "no-store",
    },
  });
}
