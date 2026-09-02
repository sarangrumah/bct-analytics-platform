/**
 * A minimal XLSX writer: OOXML SpreadsheetML inside a hand-built ZIP container.
 *
 * Written rather than installed on purpose. An export path is the one place in this application
 * where warehouse rows leave the building, and it is the last place to widen the dependency
 * surface: Security's `sca-node` job scans this project and a fixed-status HIGH is release
 * blocking, so a spreadsheet library pulled in for one function is a standing liability for the
 * life of the repo. This is roughly a hundred lines with no runtime dependencies, and the output is
 * verified by `tests/xlsx.test.mjs`, which unzips a generated workbook and reads the cells back.
 *
 * Entries are STORED (method 0) rather than deflated. Compression would save bytes on a file that
 * is opened once, and a stored entry is trivially verifiable, which matters more here.
 */

const CRC_TABLE = ((): Uint32Array => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
})();

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

interface ZipEntry {
  name: string;
  data: Uint8Array;
}

function u16(value: number): Uint8Array {
  return new Uint8Array([value & 0xff, (value >>> 8) & 0xff]);
}

function u32(value: number): Uint8Array {
  return new Uint8Array([
    value & 0xff,
    (value >>> 8) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 24) & 0xff,
  ]);
}

function concat(parts: Uint8Array[]): Uint8Array {
  let length = 0;
  for (const part of parts) length += part.length;
  const out = new Uint8Array(length);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function zip(entries: ZipEntry[]): Uint8Array {
  const encoder = new TextEncoder();
  const local: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  for (const entry of entries) {
    const name = encoder.encode(entry.name);
    const sum = crc32(entry.data);
    const header = concat([
      u32(0x04034b50),
      u16(20), // version needed
      u16(0x0800), // UTF-8 filename
      u16(0), // stored, not deflated
      u16(0), // modification time
      u16(0), // modification date
      u32(sum),
      u32(entry.data.length),
      u32(entry.data.length),
      u16(name.length),
      u16(0),
      name,
    ]);
    local.push(header, entry.data);

    central.push(
      concat([
        u32(0x02014b50),
        u16(20), // version made by
        u16(20), // version needed
        u16(0x0800),
        u16(0),
        u16(0),
        u16(0),
        u32(sum),
        u32(entry.data.length),
        u32(entry.data.length),
        u16(name.length),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(0),
        u32(offset),
        name,
      ]),
    );
    offset += header.length + entry.data.length;
  }

  const centralBytes = concat(central);
  const end = concat([
    u32(0x06054b50),
    u16(0),
    u16(0),
    u16(entries.length),
    u16(entries.length),
    u32(centralBytes.length),
    u32(offset),
    u16(0),
  ]);
  return concat([concat(local), centralBytes, end]);
}

/** XML text escaping, including the control characters XML 1.0 cannot represent at all. */
function xml(value: string): string {
  return value
    .replace(new RegExp("[\u0000-\u0008\u000B\u000C\u000E-\u001F]", "g"), "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function columnName(index: number): string {
  let name = "";
  let remaining = index + 1;
  while (remaining > 0) {
    const modulo = (remaining - 1) % 26;
    name = String.fromCharCode(65 + modulo) + name;
    remaining = Math.floor((remaining - modulo) / 26);
  }
  return name;
}

export type Cell = string | number | null;

/**
 * Build a single-sheet workbook.
 *
 * A `null` cell is written as an empty cell, not as a zero and not as the string "null". The
 * distinction carries into the export because it carries in the data: `revenue_mom_growth` has no
 * prior month for the first row of a window, and a spreadsheet that says 0 there is asserting flat
 * growth that nobody measured.
 */
export function buildXlsx(sheetName: string, rows: Cell[][]): Uint8Array {
  const encoder = new TextEncoder();
  const sheetRows = rows
    .map((row, rowIndex) => {
      const cells = row
        .map((cell, columnIndex) => {
          const reference = columnName(columnIndex) + String(rowIndex + 1);
          if (cell === null) return "";
          if (typeof cell === "number" && Number.isFinite(cell)) {
            return '<c r="' + reference + '"><v>' + String(cell) + "</v></c>";
          }
          return (
            '<c r="' +
            reference +
            '" t="inlineStr"><is><t xml:space="preserve">' +
            xml(String(cell)) +
            "</t></is></c>"
          );
        })
        .join("");
      return '<row r="' + String(rowIndex + 1) + '">' + cells + "</row>";
    })
    .join("");

  const safeSheet = xml(sheetName).slice(0, 31) === "" ? "Sheet1" : xml(sheetName).slice(0, 31);

  const files: ZipEntry[] = [
    {
      name: "[Content_Types].xml",
      data: encoder.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
          '<Default Extension="xml" ContentType="application/xml"/>' +
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
          '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
          "</Types>",
      ),
    },
    {
      name: "_rels/.rels",
      data: encoder.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
          "</Relationships>",
      ),
    },
    {
      name: "xl/workbook.xml",
      data: encoder.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ' +
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
          '<sheets><sheet name="' +
          safeSheet +
          '" sheetId="1" r:id="rId1"/></sheets>' +
          "</workbook>",
      ),
    },
    {
      name: "xl/_rels/workbook.xml.rels",
      data: encoder.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
          '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
          "</Relationships>",
      ),
    },
    {
      name: "xl/worksheets/sheet1.xml",
      data: encoder.encode(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
          "<sheetData>" +
          sheetRows +
          "</sheetData>" +
          "</worksheet>",
      ),
    },
  ];

  return zip(files);
}

/**
 * RFC 4180 CSV, with one addition that is not cosmetic.
 *
 * A cell whose text begins `=`, `+`, `-`, `@`, tab or carriage return is prefixed with an
 * apostrophe. Spreadsheet applications treat those as formulas, so an exported value can otherwise
 * become executable content on the recipient's machine. The warehouse masks personal data; it does
 * not defend the spreadsheet the data is opened in, and that is this function's job.
 */
export function buildCsv(rows: Cell[][]): string {
  const escape = (cell: Cell): string => {
    if (cell === null) return "";
    if (typeof cell === "number") return String(cell);
    let text = cell;
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    if (/[",\r\n]/.test(text)) text = '"' + text.replace(/"/g, '""') + '"';
    return text;
  };
  return rows.map((row) => row.map(escape).join(",")).join("\r\n") + "\r\n";
}
