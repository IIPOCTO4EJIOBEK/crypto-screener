#!/usr/bin/env bash
# Маршрут /api/hook в Caddy: кнопка «в сделку» на главной скринера → приёмник
# сигналов бота (127.0.0.1:8787). Секрет WEBHOOK_TOKEN подставляет Caddy в
# заголовок X-Token, в браузер он не попадает. Запуск от root, один раз:
#
#     sudo bash /opt/crypto-screener/tools/deploy/caddy_hook.sh
#
# Повторный запуск безопасен: маршрут не дублируется, токен обновляется.
set -euo pipefail
CADDYFILE=/etc/caddy/Caddyfile
ENV=/opt/crypto-trade/.env

TOKEN=$(sed -n 's/^WEBHOOK_TOKEN=//p' "$ENV" | tail -n1 | tr -d '"'"'"'\r')
if [ "${#TOKEN}" -lt 16 ]; then
  echo "нет WEBHOOK_TOKEN (16+ символов) в $ENV" >&2
  exit 1
fi

cp -a "$CADDYFILE" "$CADDYFILE.bak.$(date +%Y%m%d%H%M%S)"
python3 - "$CADDYFILE" "$TOKEN" <<'PY'
import re, sys
path, token = sys.argv[1], sys.argv[2]
s = open(path, encoding="utf-8").read()
s = re.sub(r"\n\thandle /api/hook \{.*?\n\t\}\n", "\n", s, flags=re.S)   # старый маршрут
block = ("\thandle /api/hook {\n"
         "\t\t@post method POST\n"
         "\t\treverse_proxy @post 127.0.0.1:8787 {\n"
         f"\t\t\theader_up X-Token \"{token}\"\n"
         "\t\t}\n"
         "\t\trespond 405\n"
         "\t}\n")
anchor = "\theader Cache-Control \"no-cache\"\n"
assert anchor in s, "якорь не найден в Caddyfile"
s = s.replace(anchor, anchor + block, 1)
open(path, "w", encoding="utf-8").write(s)
PY
# в файле теперь секрет: читать его может только root и caddy
chown root:caddy "$CADDYFILE"
chmod 640 "$CADDYFILE"
caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null
systemctl reload caddy
echo "готово: /api/hook → 127.0.0.1:8787"
