import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { buildCsv, buildXlsx } from "../src/lib/xlsx.ts";

/**
 * The export writers.
 *
 * The XLSX assertion actually unzips the workbook with Python's `zipfile` and reads the cell text
 * back out, rather than asserting on the bytes this module just produced. A hand-built ZIP that
 * only this project can read is not a spreadsheet, and a test that checks a writer against its own
 * output would pass on a broken container.
 */

test("csv quotes separators and newlines per RFC 4180", () => {
  const csv = buildCsv([
    ["Bulan", "Nilai"],
    ['Jan, 2026', 1000],
    ['dengan "kutip"', 2000],
    ["dua\nbaris", 3000],
  ]);
  assert.ok(csv.includes('"Jan, 2026"'));
  assert.ok(csv.includes('"dengan ""kutip"""'));
  assert.ok(csv.includes('"dua\nbaris"'));
});

test("csv neutralises spreadsheet formula injection", () => {
  const csv = buildCsv([["=1+1"], ["+cmd"], ["-2"], ["@SUM(A1)"]]);
  const lines = csv.trimEnd().split("\r\n");
  for (const line of lines) {
    assert.equal(line.startsWith("'"), true, "a formula-looking cell must be prefixed: " + line);
  }
});

test("a null cell exports as empty, never as zero", () => {
  const csv = buildCsv([
    ["Bulan", "Pertumbuhan"],
    ["Sep 2025", null],
    ["Okt 2025", 0.0018],
  ]);
  const lines = csv.trimEnd().split("\r\n");
  assert.equal(lines[1], "Sep 2025,");
  assert.equal(
    lines[1]?.endsWith(",0"),
    false,
    "no prior month is not zero growth, and the export must not say it is",
  );
});

test("a numeric cell is not quoted and keeps full precision", () => {
  const csv = buildCsv([[1234567.89]]);
  assert.equal(csv.trimEnd(), "1234567.89");
});

test("the xlsx workbook is a real zip that a spreadsheet reader can open", () => {
  const bytes = buildXlsx("revenue_net", [
    ["Bulan", "Nilai (IDR)"],
    ["Jan 2026", 44170500],
    ["Feb 2026", null],
  ]);
  const dir = mkdtempSync(join(tmpdir(), "portal-xlsx-"));
  const file = join(dir, "book.xlsx");
  writeFileSync(file, bytes);

  const script = [
    "import zipfile, sys, re",
    "z = zipfile.ZipFile(sys.argv[1])",
    "bad = z.testzip()",
    "assert bad is None, bad",
    "names = set(z.namelist())",
    "assert '[Content_Types].xml' in names, names",
    "assert 'xl/worksheets/sheet1.xml' in names, names",
    "sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')",
    "print('CELLS:' + '|'.join(re.findall(r'<t[^>]*>([^<]*)</t>', sheet)))",
    "print('VALUES:' + '|'.join(re.findall(r'<v>([^<]*)</v>', sheet)))",
    "print('ROWS:' + str(sheet.count('<row ')))",
  ].join("\n");
  const scriptFile = join(dir, "read.py");
  writeFileSync(scriptFile, script);

  const output = execFileSync("python3", [scriptFile, file], { encoding: "utf8" });
  assert.match(output, /CELLS:Bulan\|Nilai \(IDR\)\|Jan 2026/);
  assert.match(output, /VALUES:44170500/);
  assert.match(output, /ROWS:3/);
});

test("xml special characters survive into the workbook", () => {
  const bytes = buildXlsx("s", [["a & b <c>"]]);
  const dir = mkdtempSync(join(tmpdir(), "portal-xlsx-"));
  const file = join(dir, "book.xlsx");
  writeFileSync(file, bytes);
  const scriptFile = join(dir, "read.py");
  writeFileSync(
    scriptFile,
    [
      "import zipfile, sys, re",
      "z = zipfile.ZipFile(sys.argv[1])",
      "sheet = z.read('xl/worksheets/sheet1.xml').decode('utf-8')",
      "print(re.findall(r'<t[^>]*>([^<]*)</t>', sheet)[0])",
    ].join("\n"),
  );
  const output = execFileSync("python3", [scriptFile, file], { encoding: "utf8" }).trim();
  assert.equal(output, "a &amp; b &lt;c&gt;");
});
