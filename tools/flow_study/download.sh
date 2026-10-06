#!/bin/bash
# Download aggTrades + klines from data.binance.vision (public, no key).
# Day-major ordering (newest day first, all 4 symbols together) so that a
# partial run still yields complete days for every symbol.
# Direct first (public CDN), proxy as fallback. Caches: skips existing files.
BASE="https://data.binance.vision/data/futures/um/daily"
R="/mnt/data/binance-archive/daily"
LOG="/tmp/flow/download.log"
: > "$LOG"

fetch() {
  local url="$1" dest="$2" name="$3"
  [ -s "$dest" ] && { echo "SKIP $name" >> "$LOG"; return 0; }
  mkdir -p "$(dirname "$dest")"
  # proxy FIRST: the direct route to data.binance.vision stalls (blocked);
  # short timeouts so a hanging attempt falls through quickly.
  if curl -sS -f --connect-timeout 12 --max-time 200 --retry 1 --retry-delay 2 \
        -o "$dest.part" "$url" \
     || curl -sS -f --noproxy '*' --connect-timeout 8 --max-time 25 \
        -o "$dest.part" "$url"; then
    mv "$dest.part" "$dest"
    echo "OK   $name $(stat -c%s "$dest")" >> "$LOG"
  else
    rm -f "$dest.part"
    echo "FAIL $name $url" >> "$LOG"
  fi
}
export -f fetch
export LOG

JOBS=()
for i in $(seq 0 19); do
  d=$(date -u -d "2026-09-28 - $i day" +%Y-%m-%d)
  for s in BTCUSDT ETHUSDT SOLUSDT DOGEUSDT; do
    JOBS+=("$s|$d")
  done
done
echo "jobs=${#JOBS[@]}" >> "$LOG"

printf '%s\n' "${JOBS[@]}" | xargs -P 8 -I{} bash -c '
  IFS="|" read s d <<< "{}"
  B="https://data.binance.vision/data/futures/um/daily"
  R="/mnt/data/binance-archive/daily"
  fetch "$B/aggTrades/$s/$s-aggTrades-$d.zip" "$R/aggTrades/$s/$s-aggTrades-$d.zip" "aggTrades $s $d"
  fetch "$B/klines/$s/1m/$s-1m-$d.zip" "$R/klines/$s/1m/$s-1m-$d.zip" "klines $s $d"
'
echo "=== DONE ===" >> "$LOG"
echo "ok=$(grep -c '^OK' "$LOG") fail=$(grep -c '^FAIL' "$LOG")" >> "$LOG"
