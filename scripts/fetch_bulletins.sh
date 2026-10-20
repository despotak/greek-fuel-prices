#!/usr/bin/env bash
# Mirror fuelprices.gr per-prefecture daily bulletins into ../pdf/<YYYY-MM-DD>.pdf.
# Incremental: files already present are skipped, so a rerun fetches only new days.
# Two workers and a short pause per file, to stay polite to a ministry server.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/pdf"
index="$(mktemp)"
trap 'rm -f "$index"' EXIT

curl -sSfL -A 'Mozilla/5.0' 'https://www.fuelprices.gr/deltia_dn.view' \
  | iconv -f windows-1253 -t utf-8 \
  | grep -aoE 'deltia/IMERISIO_DELTIO_ANA_NOMO_[0-9]{1,2}_[0-9]{2}_[0-9]{4}\.pdf' \
  | sort -u \
  | sed -E 's#^(.*_([0-9]{1,2})_([0-9]{2})_([0-9]{4})\.pdf)$#\4-\3-\2 \1#' \
  | awk '{ split($1, d, "-"); printf "%s-%s-%02d %s\n", d[1], d[2], d[3], $2 }' > "$index"

echo "$(wc -l < "$index") bulletins listed, $(find "$root/pdf" -name '*.pdf' | wc -l) already here"

cd "$root/pdf"
xargs -P 2 -L 1 sh -c '
  f="$0.pdf"; [ -s "$f" ] && exit 0
  curl -sSfL --retry 3 --retry-delay 2 -A "Mozilla/5.0" "https://www.fuelprices.gr/$1" -o "$f.part" \
    && mv "$f.part" "$f" || { echo "FAIL $0 $1"; rm -f "$f.part"; }
  sleep 0.2' < "$index"

echo "done: $(find "$root/pdf" -name '*.pdf' | wc -l) bulletins"
