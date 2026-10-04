#!/usr/bin/env bash
# Развернуть бумажных ботов по скринеру в ВМ одной командой (без sudo).
# Запуск в ВМ из /opt/crypto-trade:  bash tools/trade/deploy_vm.sh
# Повторный запуск безопасен: профили и строки crontab не дублируются,
# существующие журналы и состояние ботов не трогаются. Живой режим не включается.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python
SDB=${SCREENER_DB:-/opt/crypto-screener/data/screener.db}
TREND=${SCREENER_TREND:-/opt/crypto-screener/docs/live/trend-now.json}
UNI=/opt/crypto-screener/data/universe-turnover.json

echo "== 1. тесты"
$PY -m pytest -q tests/test_screener_bot.py tests/test_trade.py | tail -2

echo "== 2. пути скринера"
[ -r "$SDB" ] && echo "база: $SDB" || { echo "НЕТ базы скринера: $SDB (задай SCREENER_DB=...)"; exit 1; }
[ -r "$TREND" ] && echo "тренды: $TREND" || echo "нет файла трендов $TREND — варианты с фильтром тренда не будут входить"
EXTRA="\"--db\", \"$SDB\", \"--trend-src\", \"$TREND\""
[ -r "$UNI" ] && EXTRA="$EXTRA, \"--universe\", \"$UNI\"" && echo "вселенная: $UNI"

echo "== 3. профили"
mkdir -p data/trade
P=data/trade/screener-profiles.json
if [ ! -f "$P" ]; then
  cat > "$P" <<JSON
[[$EXTRA, "--name", "all"],
 [$EXTRA, "--name", "all-trend-tf", "--trend", "tf"],
 [$EXTRA, "--name", "managed", "--trend", "tf", "--tp1", "1", "--be-after-tp1",
  "--trail", "1.5", "--no-target", "--daily-loss", "0.03", "--cooldown", "30",
  "--pause-after", "3", "--max-side", "3", "--funding", "--notify"]]
JSON
  echo "записан $P"
else
  echo "$P уже есть — не трогаю"
fi

echo "== 4. секрет вебхука"
touch .env
if ! grep -q '^WEBHOOK_TOKEN=' .env; then
  echo "WEBHOOK_TOKEN=$($PY -c 'import secrets; print(secrets.token_hex(24))')" >> .env
  echo "WEBHOOK_TOKEN добавлен в .env"
else
  echo "WEBHOOK_TOKEN уже есть"
fi
grep -q '^TELEGRAM_BOT_TOKEN=.\+' .env && echo "Telegram: задан" || echo "Telegram: не задан (TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env)"

echo "== 5. первый круг"
START=$(date +%s)
$PY -X utf8 -m tools.trade.screener_bot || true
echo "круг занял $(( $(date +%s) - START )) с"
for n in all all-trend-tf managed; do
  echo "-- $n"; $PY -X utf8 -m tools.trade.screener_bot --name "$n" --status || true
done

echo "== 6. crontab"
DIR=$(pwd)
L1="*/2 * * * * cd $DIR && flock -n /tmp/screener-bot.lock $PY -X utf8 -m tools.trade.screener_bot >> data/trade/screener-bot.log 2>&1"
L2="@reboot cd $DIR && $PY -X utf8 -m tools.trade.webhook >> data/trade/webhook.log 2>&1"
# без set -e внутри: у нового пользователя crontab пуст, и grep без строк
# возвращает 1 — раньше это молча обрывало скрипт на этом шаге
TMP=$(mktemp)
{ crontab -l 2>/dev/null || true; } | { grep -v -e 'tools.trade.screener_bot' -e 'tools.trade.webhook' || true; } > "$TMP"
echo "$L1" >> "$TMP"
echo "$L2" >> "$TMP"
crontab "$TMP"
rm -f "$TMP"
crontab -l | grep tools.trade || echo "crontab не записался"

echo "== 7. вебхук"
if ! pgrep -f 'tools.trade.webhook' >/dev/null; then
  nohup $PY -X utf8 -m tools.trade.webhook < /dev/null >> data/trade/webhook.log 2>&1 &
  sleep 1
fi
pgrep -f 'tools.trade.webhook' >/dev/null && echo "вебхук слушает 127.0.0.1:8787" || echo "вебхук не запустился, см. data/trade/webhook.log"

echo "== 8. проверка правил выхода на архиве (в фоне)"
if $PY -c "import requests; requests.head('https://data.binance.vision', timeout=10).raise_for_status()" 2>/dev/null; then
  nohup $PY -X utf8 -m tools.exit_study --days 45 < /dev/null > data/trade/exit_study.txt 2>&1 &
  echo "запущена, итог: data/trade/exit_study.txt"
else
  echo "data.binance.vision недоступен — проверку правил выхода пропускаю"
fi
echo "ГОТОВО. Всё бумажное, заявки на биржу не уходят."
