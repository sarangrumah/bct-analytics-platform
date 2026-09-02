/**
 * Display formatting only.
 *
 * Formatting a number the semantic layer returned is presentation. Deriving a new number is not,
 * and nothing in this file does it: there is no addition, no division and no aggregation here.
 * `Intl.NumberFormat` with a fixed locale keeps the server render and the client hydration
 * identical, which a locale read from the request would not.
 */

const IDR = new Intl.NumberFormat("id-ID", {
  style: "currency",
  currency: "IDR",
  maximumFractionDigits: 0,
});

const IDR_COMPACT = new Intl.NumberFormat("id-ID", {
  style: "currency",
  currency: "IDR",
  notation: "compact",
  maximumFractionDigits: 1,
});

const PLAIN = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
const PERCENT = new Intl.NumberFormat("id-ID", {
  style: "percent",
  maximumFractionDigits: 1,
  signDisplay: "exceptZero",
});
const PERCENT_PLAIN = new Intl.NumberFormat("id-ID", {
  style: "percent",
  maximumFractionDigits: 1,
});
const PLAIN_COMPACT = new Intl.NumberFormat("id-ID", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Format one measure using the unit the API reported for it. */
export function formatValue(value: number, unit: string | null): string {
  if (unit === "IDR") return IDR.format(value);
  if (unit === "unit") return PLAIN.format(value) + " unit";
  return PLAIN.format(value);
}

/**
 * Format a measure using BOTH `meta.unit` and `meta.type`.
 *
 * `percent` metrics carry `unit: null`, so unit alone cannot tell a rate from a count: rendering
 * `ppob_success_rate` as "0,98" instead of "98,4%" is the kind of quiet misreading that survives
 * review. `signDisplay: "exceptZero"` is used only for growth, where the sign IS the reading.
 *
 * A null measure prints an em dash. `revenue_mom_growth` returns null for the first month of a
 * window because there is no prior month; printing 0 there would assert flat growth that nobody
 * measured.
 */
export function formatMeasure(
  value: number | null,
  meta: { unit: string | null; type: string },
  options: { signed?: boolean } = {},
): string {
  if (value === null) return "—";
  if (meta.type === "percent") {
    return options.signed === true ? PERCENT.format(value) : PERCENT_PLAIN.format(value);
  }
  return formatValue(value, meta.unit);
}

export function formatCompact(value: number, unit: string | null, type?: string): string {
  if (type === "percent") return PERCENT_PLAIN.format(value);
  if (unit === "IDR") return IDR_COMPACT.format(value);
  return PLAIN_COMPACT.format(value);
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
  "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
];

/** `2026-01-01` to `Jan 2026`. Pure string work; no timezone conversion is applied to a date-only value. */
export function formatMonth(value: string): string {
  if (!/^\d{4}-\d{2}/.test(value)) return value;
  const year = value.slice(0, 4);
  const month = Number.parseInt(value.slice(5, 7), 10);
  return (MONTHS[month - 1] ?? value) + " " + year;
}

export function formatDay(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}/.test(value)) return value;
  const day = value.slice(8, 10);
  const month = Number.parseInt(value.slice(5, 7), 10);
  return day + " " + (MONTHS[month - 1] ?? "") + " " + value.slice(0, 4);
}

/**
 * The pipeline timestamp, shown as an absolute instant in UTC.
 *
 * Deliberately absolute. A relative rendering ("4 minutes ago") would be the viewer's clock doing
 * arithmetic on a pipeline fact, and the one thing this dashboard must not do about freshness is
 * substitute a clock for the pipeline. Staleness itself comes from `meta.is_stale`, which the
 * warehouse decided.
 */
export function formatRefreshedAt(value: string | null): string {
  if (value === null) return "tidak diketahui";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n: number): string => String(n).padStart(2, "0");
  return (
    parsed.getUTCFullYear() +
    "-" + pad(parsed.getUTCMonth() + 1) +
    "-" + pad(parsed.getUTCDate()) +
    " " + pad(parsed.getUTCHours()) +
    ":" + pad(parsed.getUTCMinutes()) +
    ":" + pad(parsed.getUTCSeconds()) +
    " UTC"
  );
}

/** "60 detik" / "15 menit" / "60 menit". Describes the SLA the API reported; derives nothing. */
export function formatSla(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return seconds / 3600 + " jam";
  // Below two minutes the number of seconds IS the story. PPOB's SLA is 60 s and rendering it as
  // "1 menit" made the tightest SLA in the platform read like the loosest unit of measurement -
  // which is exactly the distinction ADR 0001 spends a table making.
  if (seconds < 120) return seconds + " detik";
  return Math.round(seconds / 60) + " menit";
}

const DIMENSION_LABELS: Record<string, string> = {
  date_day: "Tanggal",
  date_month: "Bulan",
  tenant_id: "Tenant",
  operating_unit_id: "Operating Unit",
  partner_key: "Mitra",
  product_key: "Produk",
  product_id: "ID Produk",
  company_id: "Perusahaan",
  revenue_channel: "Kanal",
  biller_key: "Biller",
  biller_code: "Kode Biller",
  biller_category: "Kategori Biller",
  state: "Status",
  account_id: "Akun",
  move_type: "Jenis Jurnal",
  payment_state: "Status Pembayaran",
  is_revenue_line: "Baris Pendapatan",
  has_unit_cost: "Punya Harga Pokok",
  account_type: "Jenis Akun",
  is_profit_and_loss: "Laba Rugi",
  value: "Nilai",
};

/**
 * Odoo journal entry types, spelled out. The raw codes are what the warehouse stores and what the
 * export contains; the screen shows them expanded so a reader does not have to know the schema.
 */
const MOVE_TYPES: Record<string, string> = {
  entry: "Jurnal umum",
  out_invoice: "Faktur pelanggan",
  out_refund: "Nota kredit pelanggan",
  in_invoice: "Tagihan pemasok",
  in_refund: "Nota kredit pemasok",
  out_receipt: "Kuitansi penjualan",
  in_receipt: "Kuitansi pembelian",
};

const PAYMENT_STATES: Record<string, string> = {
  not_paid: "Belum dibayar",
  in_payment: "Dalam proses",
  paid: "Lunas",
  partial: "Sebagian",
  reversed: "Dibalik",
  invoicing_legacy: "Legacy",
};

export function dimensionLabel(name: string): string {
  return DIMENSION_LABELS[name] ?? name;
}

/**
 * Render a dimension cell.
 *
 * `operating_unit_id === -1` is the explicit UNASSIGNED member of `dim_operating_unit`, not a
 * missing value, and it is labelled as such so a viewer does not read it as a bug.
 */
export function formatDimension(
  dimension: string,
  value: string | number | boolean | null,
): string {
  /**
   * NULL on `is_profit_and_loss` means "neither profit-and-loss nor balance sheet" - section and
   * note lines carry no account - and emphatically not `false`. It is labelled rather than shown
   * as an em dash so nobody reads the group as a rendering gap. This seed happens to contain zero
   * such rows, which is not evidence that they cannot occur.
   */
  if (dimension === "is_profit_and_loss" && value === null) return "Bukan keduanya (NULL)";
  if (value === null) return "—";
  if (dimension === "operating_unit_id" && value === -1) return "Tanpa Operating Unit";
  if (dimension === "date_month" && typeof value === "string") return formatMonth(value);
  if (dimension === "date_day" && typeof value === "string") return formatDay(value);
  if (dimension === "move_type" && typeof value === "string") {
    return MOVE_TYPES[value] ?? value;
  }
  if (dimension === "payment_state" && typeof value === "string") {
    return PAYMENT_STATES[value] ?? value;
  }
  if (
    dimension === "is_revenue_line" ||
    dimension === "has_unit_cost" ||
    dimension === "is_profit_and_loss"
  ) {
    if (value === true || value === "true" || value === 1) return "Ya";
    if (value === false || value === "false" || value === 0) return "Tidak";
  }
  if (dimension === "account_id") return "Akun " + String(value);
  return String(value);
}
