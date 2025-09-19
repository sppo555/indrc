#!/usr/bin/env bash
set -euo pipefail

# generate-nginx-from-template.sh
# Usage:
#   ./generate-nginx-from-template.sh TEMPLATE_FILE INPUT_TXT OUTPUT_DIR
#   （或不帶參數，進入互動式輸入路徑模式）
#
# INPUT_TXT format per line (space-separated):
#   <number> <domain>
# examples:
#   101 www.fd5tytrf.com
#   102 www.uyhk76t.com
#
# The script replaces:
#   - all occurrences of the domain placeholder with <domain>
#   - all occurrences of the prod placeholder with <number>
#     (so with template path '.../prod__PROD_PLACEHOLDER__' it becomes '.../prod<number>')

usage() {
  echo "Usage: $0 TEMPLATE_FILE INPUT_TXT OUTPUT_DIR" >&2
  echo "或：直接執行 $0 後依照提示輸入路徑" >&2
  exit 1
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
fi

if [[ $# -eq 0 ]]; then
  # Interactive mode
  read -r -p "Template file path: " TEMPLATE_FILE
  read -r -p "Sites list file path: " INPUT_TXT
  read -r -p "Output directory: " OUTPUT_DIR
elif [[ $# -eq 3 ]]; then
  TEMPLATE_FILE="$1"
  INPUT_TXT="$2"
  OUTPUT_DIR="$3"
else
  usage
fi

if [[ ! -f "$TEMPLATE_FILE" ]]; then
  echo "[ERROR] Template file not found: $TEMPLATE_FILE" >&2
  exit 2
fi
if [[ ! -f "$INPUT_TXT" ]]; then
  echo "[ERROR] Input list not found: $INPUT_TXT" >&2
  exit 3
fi
mkdir -p "$OUTPUT_DIR"

# Fixed placeholders in the template
# Must match the tokens used in nginx-template.conf
PLACEHOLDER_DOMAIN="__DOMAIN_PLACEHOLDER__"
PLACEHOLDER_PROD="__PROD_PLACEHOLDER__"

# Read line by line
# - ignore empty lines and lines starting with '#'
# - allow multiple whitespaces or tabs as separator
while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip comments and empty lines (allow leading whitespace)
  echo "$line" | grep -Eq "^\s*$" && continue
  echo "$line" | grep -Eq "^\s*#" && continue

  # Split into fields with awk (handles multiple spaces/tabs)
  num=$(echo "$line" | awk '{print $1}')
  domain=$(echo "$line" | awk '{print $2}')

  if [[ -z "$num" || -z "$domain" ]]; then
    echo "[WARN] Skip invalid line (need 2 fields): $line" >&2
    continue
  fi

  # Validate number is numeric (digits only)
  if ! echo "$num" | grep -Eq '^[0-9]+$'; then
    echo "[WARN] Skip line, first field must be a number: $line" >&2
    continue
  fi

  outFile="$OUTPUT_DIR/${domain}.conf"

  # Do replacements safely using sed
  # 1) replace domain placeholder everywhere
  # 2) replace prod placeholder with prod<digits>
  # Use a temp file to avoid in-place issues
  tmp=$(mktemp)
  sed "s/${PLACEHOLDER_DOMAIN//\//\\/}/${domain//\//\\/}/g" "$TEMPLATE_FILE" > "$tmp"
  sed "s/${PLACEHOLDER_PROD}/${num}/g" "$tmp" > "$outFile"
  rm -f "$tmp"

  echo "[OK] Generated: $outFile (domain=${domain}, prod=prod${num})"

done < "$INPUT_TXT"

echo "Done. Files are in: $OUTPUT_DIR"
