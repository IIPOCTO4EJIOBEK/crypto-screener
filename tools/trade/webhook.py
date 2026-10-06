"""Приёмник сигналов: вебхук TradingView и кнопка «в сделку» на странице скринера.

    python -m tools.trade.webhook --port 8787

POST /hook?bot=managed  с JSON (формат — src/trade/inbox.py) и секретом:
заголовок `X-Token: <WEBHOOK_TOKEN>` или поле "token" в теле (TradingView
заголовков не задаёт). Без верного секрета — 403. Сигнал кладётся в
data/trade/screener-<bot>/inbox/ и исполняется ботом на ближайшем круге
(раз в 2 минуты), на бумаге.

Секрет — WEBHOOK_TOKEN в .env. Если его нет, приёмник не запускается.
Наружу порт не открывается сам: доступ из интернета (для TradingView) —
только через обратный прокси с HTTPS, по отдельному решению.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.trade import inbox                                   # noqa: E402

MAX_BODY = 8192


def make_handler(token: str, trade_root: Path, default_bot: str = "managed"):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlparse(self.path).path.rstrip("/") != "/api/levels":
                return self._send(404, {"ok": False})
            if not hmac.compare_digest(self.headers.get("X-Token", "").encode(), token.encode()):
                return self._send(403, {"ok": False})
            from src.data.manual_levels import Store
            self._send(200, dict(ok=True, **Store(os.environ.get("MANUAL_LEVELS_DB", "/var/lib/screener/manual-levels.db")).snapshot()))

        def do_POST(self):                                     # noqa: N802
            url = urlparse(self.path)
            if url.path.rstrip("/") not in ("/hook", "/api/hook", "/api/levels", "/api/position"):
                return self._send(404, {"ok": False, "error": "нет такого адреса"})
            try: n = int(self.headers.get("Content-Length") or 0)
            except ValueError: return self._send(400, {"ok": False, "error": "неверный размер тела"})
            if n <= 0 or n > MAX_BODY:
                return self._send(400, {"ok": False, "error": "пустое или слишком большое тело"})
            try:
                msg = json.loads(self.rfile.read(n))
                assert isinstance(msg, dict)
            except Exception:                                  # noqa: BLE001
                return self._send(400, {"ok": False, "error": "тело — не JSON-объект"})
            got = self.headers.get("X-Token") or str(msg.pop("token", ""))
            if not hmac.compare_digest(got.encode(), token.encode()):
                return self._send(403, {"ok": False, "error": "неверный секрет"})
            if url.path.rstrip("/") == "/api/levels":
                from src.data.manual_levels import Store
                store = Store(os.environ.get("MANUAL_LEVELS_DB", "/var/lib/screener/manual-levels.db"))
                try:
                    op = msg.get("op")
                    if op == "save": result = store.save(msg.get("item") or {})
                    elif op in ("delete", "rearm", "ack"): store.change(op, str(msg.get("id", ""))); result = {}
                    else: raise ValueError("unknown operation")
                except (ValueError, TypeError, KeyError):
                    return self._send(400, {"ok": False, "error": "проверьте цену, монету и параметры уровня"})
                return self._send(200, {"ok": True, "item": result})
            bot = (parse_qs(url.query).get("bot") or [default_bot])[0]
            if not re.fullmatch(r"[a-z0-9-]{1,40}", bot):
                return self._send(400, {"ok": False, "error": "плохое имя бота"})
            root = trade_root / f"screener-{bot}"
            if not (root / "state.json").exists():
                return self._send(404, {"ok": False, "error": f"бота {bot} нет"})
            if url.path.rstrip("/") == "/api/position":
                from src.trade.position_controls import enqueue
                try: queued = enqueue(root, msg)
                except (ValueError, TypeError, KeyError): return self._send(400, {"ok": False, "error": "проверьте уровни и укажите причину (3–300 символов)"})
                return self._send(200, {"ok": True, "queued": queued, "note": "команда в очереди; бот проверит цену и сохранит причину на ближайшем цикле"})
            if not msg.get("symbol") and not msg.get("ticker"):
                return self._send(400, {"ok": False, "error": "нет symbol"})
            msg.setdefault("source", "webhook")
            path = inbox.put(root, msg)
            return self._send(200, {"ok": True, "bot": bot, "queued": path.name,
                                    "note": "исполнится на ближайшем круге бота (бумага)"})

        def log_message(self, fmt, *args):                     # тише в журнале
            sys.stderr.write("webhook: " + fmt % args + "\n")

    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--bot", default="managed")
    a = ap.parse_args(argv)
    from tools.trade.run import load_env
    load_env()
    token = os.environ.get("WEBHOOK_TOKEN", "")
    if len(token) < 16:
        print("нет WEBHOOK_TOKEN (16+ символов) в .env — приёмник не запущен", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer((a.host, a.port),
                              make_handler(token, ROOT / "data" / "trade", a.bot))
    print(f"вебхук слушает http://{a.host}:{a.port}/hook", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
