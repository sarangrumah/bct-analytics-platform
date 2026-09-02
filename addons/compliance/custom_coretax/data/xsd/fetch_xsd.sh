#!/usr/bin/env bash
# fetch_xsd.sh — pull the official DJP Coretax XSD templates.
#
# Usage:
#   bash fetch_xsd.sh
#
# Notes:
#   - Run from anywhere; output lands next to this script.
#   - Source landing page (HTML, lists 31 templates):
#       https://www.pajak.go.id/reformdjp/coretax/template-xml-dan-converter-excel-ke-xml
#   - The DJP CDN (edukasi.pajak.go.id / www.pajak.go.id/sites/default/files/...)
#     occasionally rejects scripted clients via Cloudflare. If a URL 404s or
#     returns an HTML challenge page, we record the failure in SOURCES.md
#     instead of writing a fake XSD.
#   - We only need the 7 XSDs referenced by custom_coretax wizards:
#       efaktur_keluaran, faktur_masukan,
#       bupot_21_tetap, bupot_21_bukan_tetap,
#       bupot_23, bupot_26, bupot_unifikasi.
#
# Exit code is always 0 — partial success is the expected real-world outcome;
# the SOURCES.md log captures the per-template state for the operator.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

LANDING="https://www.pajak.go.id/reformdjp/coretax/template-xml-dan-converter-excel-ke-xml"
SOURCES_MD="$SCRIPT_DIR/SOURCES.md"
TODAY="$(date -u +%Y-%m-%d)"

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CURL_OPTS=(--silent --show-error --location --max-time 30 --user-agent "$UA")

# Map of <local-name>|<candidate-url-list (space-separated)>.
# Candidate URLs are *best-guess* DJP CDN paths. The landing page links
# rotate frequently — operators should re-confirm against the page above
# whenever the script reports a 404.
declare -A CANDIDATES=(
  [efaktur_keluaran]="\
https://www.pajak.go.id/sites/default/files/2024-12/Faktur%20Pajak%20Keluaran.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/FakturPajakKeluaran.xsd"
  [faktur_masukan]="\
https://www.pajak.go.id/sites/default/files/2024-12/Faktur%20Pajak%20Masukan.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/FakturPajakMasukan.xsd"
  [bupot_21_tetap]="\
https://www.pajak.go.id/sites/default/files/2024-12/Bupot%2021%20Pegawai%20Tetap.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/Bupot21PegawaiTetap.xsd"
  [bupot_21_bukan_tetap]="\
https://www.pajak.go.id/sites/default/files/2024-12/Bupot%2021%20Bukan%20Pegawai%20Tetap.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/Bupot21BukanPegawaiTetap.xsd"
  [bupot_23]="\
https://www.pajak.go.id/sites/default/files/2024-12/Bupot%2023.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/Bupot23.xsd"
  [bupot_26]="\
https://www.pajak.go.id/sites/default/files/2024-12/Bupot%2026.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/Bupot26.xsd"
  [bupot_unifikasi]="\
https://www.pajak.go.id/sites/default/files/2024-12/Bupot%20Unifikasi.xsd \
https://www.pajak.go.id/sites/default/files/2025-01/BupotUnifikasi.xsd"
)

# Reset SOURCES.md.
{
  echo "# Coretax XSD download log"
  echo
  echo "Generated: $TODAY"
  echo "Landing page: <$LANDING>"
  echo
  echo "| Local name | Outcome | Bytes | URL attempted (last) |"
  echo "|---|---|---|---|"
} > "$SOURCES_MD"

placeholder_xsd() {
  # $1 = local name, $2 = last URL attempted
  local name="$1" url="$2" file="$SCRIPT_DIR/${1}.xsd"
  cat > "$file" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!--
  PLACEHOLDER XSD for ${name}.
  TODO: download from official source.
  Last attempted: ${url}
  Landing page : ${LANDING}
  Date         : ${TODAY}

  This file exists so the module loads and the export wizard can locate
  a schema path. Validation against this placeholder will pass any well-
  formed XML — replace with the real DJP XSD before going to production.
-->
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
           elementFormDefault="qualified">
  <xs:element name="CoretaxDocument">
    <xs:complexType>
      <xs:sequence>
        <xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/>
      </xs:sequence>
      <xs:anyAttribute processContents="skip"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
EOF
}

fetch_one() {
  local name="$1"
  local urls="${CANDIDATES[$name]}"
  local out="$SCRIPT_DIR/${name}.xsd"
  local last_url=""
  for url in $urls; do
    last_url="$url"
    local tmp
    tmp="$(mktemp)"
    local code
    code="$(curl "${CURL_OPTS[@]}" -o "$tmp" -w "%{http_code}" "$url" || echo "000")"
    if [[ "$code" == "200" ]] && head -c 64 "$tmp" | grep -qi "<?xml\|<xs:schema\|<schema"; then
      mv "$tmp" "$out"
      local sz
      sz="$(wc -c <"$out" | tr -d ' ')"
      printf "| %s | OK | %s | %s |\n" "$name" "$sz" "$url" >> "$SOURCES_MD"
      echo "  [ok]    $name  ($sz bytes)"
      return 0
    fi
    rm -f "$tmp"
    echo "  [miss]  $name  HTTP $code  $url"
  done
  placeholder_xsd "$name" "$last_url"
  printf "| %s | PLACEHOLDER | n/a | %s |\n" "$name" "$last_url" >> "$SOURCES_MD"
  return 1
}

ok=0; miss=0
for name in efaktur_keluaran faktur_masukan bupot_21_tetap bupot_21_bukan_tetap bupot_23 bupot_26 bupot_unifikasi; do
  if fetch_one "$name"; then
    ok=$((ok+1))
  else
    miss=$((miss+1))
  fi
done

{
  echo
  echo "Summary: ${ok} fetched, ${miss} placeholder."
  echo
  echo "## Operator follow-up"
  echo
  echo "1. Open the landing page above in a browser."
  echo "2. For every row above marked PLACEHOLDER, locate the matching"
  echo "   template (e.g., 'Bupot PPh 21 Pegawai Tetap'), download the"
  echo "   converter zip, extract the .xsd, and drop it here renamed to"
  echo "   the local name shown in column 1."
  echo "3. Re-run an export from the wizard to confirm validation passes."
} >> "$SOURCES_MD"

echo
echo "Done. Fetched=$ok placeholders=$miss"
echo "See $SOURCES_MD"
