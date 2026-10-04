"""Алерты скринера в Telegram.

События (каждое — не чаще раза на монету и сторону, см. `COOLDOWN`):

* плотность съедена — сверка стакана (`dens_watch`) видит, что крупная
  заявка ушла сделками;
* всплеск объёма — закрытая 5м свеча с оборотом от `SPIKE`× среднего за
  2 часа до неё;
* цена у хая или лоя дня — ближе `NEAR_DAY` %;
* новый сетап «можно торговать» — свежий сработавший сигнал по тренду своего
  ТФ, R:R от 1, как на главной.

Куда слать: токен бота `TELEGRAM_BOT_TOKEN` и чат `SCREENER_ALERT_CHAT`
(по умолчанию канал проекта) берутся из окружения или из
`/opt/crypto-screener/.env`. Бот должен быть администратором канала.
Без токена модуль молчит и один раз пишет об этом в журнал.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHAT = "-1004297324007"
COOLDOWN = 3600.0           # одно и то же событие по монете — не чаще раза в час
SPIKE = 3.0
NEAR_DAY = 0.3
MIN_DENS = 100_000          # съеденные плотности меньше этого не шлём
MAX_PER_MIN = 15            # потолок сообщений в минуту, чтобы не залить канал


def _env() -> dict[str, str]:
    out = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)", line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    out.update({k: v for k, v in os.environ.items() if k in ("TELEGRAM_BOT_TOKEN", "SCREENER_ALERT_CHAT")})
    return out


def _fmt(p: float) -> str:
    a = abs(p)
    d = 1 if a >= 1000 else 2 if a >= 100 else 3 if a >= 1 else 5 if a >= 0.01 else 7
    return f"{p:,.{d}f}".replace(",", " ")


def _money(v: float) -> str:
    return f"{v / 1e6:.1f} млн" if v >= 1e6 else f"{v / 1e3:.0f} тыс"


class Alerts:
    def __init__(self, out_dir: Path):
        env = _env()
        self.token = env.get("TELEGRAM_BOT_TOKEN")
        self.chat = env.get("SCREENER_ALERT_CHAT") or DEFAULT_CHAT
        self.state_path = out_dir / "alerts_sent.json"
        try:
            self.sent: dict[str, float] = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            self.sent = {}
        self.window: list[float] = []
        self.warned = False
        self.dens_prev: dict[str, str] = {}
        self.forms_seen: set[str] = set()
        self.forms_mtime = 0.0
        # отправка — в своём потоке: живой поток свечей не должен ждать Telegram
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------------------------------------------------------- отправка
    def _send(self, key: str, text: str) -> None:
        now = time.time()
        if now - self.sent.get(key, 0) < COOLDOWN:
            return
        if not self.token:
            if not self.warned:
                print("alerts: нет TELEGRAM_BOT_TOKEN в /opt/crypto-screener/.env — алерты не уходят", flush=True)
                self.warned = True
            return
        self.window = [t for t in self.window if now - t < 60]
        if len(self.window) >= MAX_PER_MIN:
            return
        self.window.append(now)
        self.sent[key] = now
        self.q.put((key, text))

    def _worker(self) -> None:
        while True:
            key, text = self.q.get()
            self._post(key, text)

    def _post(self, key: str, text: str) -> None:
        now = time.time()
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              data={"chat_id": self.chat, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"}, timeout=10)
            if r.status_code != 200:
                print(f"alerts: Telegram {r.status_code}: {r.text[:200]}", flush=True)
                return
        except Exception as e:                      # noqa: BLE001
            print(f"alerts: Telegram недоступен: {type(e).__name__}", flush=True)
            return
        self.sent = {k: v for k, v in self.sent.items() if now - v < 86400}
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(self.sent), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except Exception:                           # noqa: BLE001
            pass

    # ---------------------------------------------------------------- события
    def candle_closed(self, sym: str, tf: str, closed: list, prev: list[list]) -> None:
        """Всплеск объёма на закрытой 5м свече: оборот к среднему за 2 часа до неё."""
        if tf != "5m" or len(prev) < 12:
            return
        base = [c[5] for c in prev[-24:] if c[5]]
        avg = sum(base) / len(base) if base else 0
        if not avg or closed[5] < SPIKE * avg:
            return
        ch = (closed[4] - closed[1]) / closed[1] * 100 if closed[1] else 0
        side = "up" if ch >= 0 else "down"
        self._send(f"spike|{sym}|{side}",
                   f"📊 <b>{sym.removesuffix('USDT')}</b> всплеск объёма 5м: {closed[5] / avg:.1f}× к среднему, "
                   f"{_money(closed[5])} $, свеча {ch:+.2f}%, цена {_fmt(closed[4])}")

    def dens_checked(self, state: dict, snapshot: dict[str, list[dict]]) -> None:
        """Плотность съедена: в прошлую сверку стояла, сейчас ушла сделками."""
        from tools.live import dens_watch
        items = state.get("items", {})
        for sym, ds in snapshot.items():
            for d in ds:
                k = dens_watch.key(sym, d)
                st = (items.get(k) or {}).get("s")
                if st == "eaten" and self.dens_prev.get(k) == "live" and (d.get("notional") or 0) >= MIN_DENS:
                    side = "продажу" if d["side"] == "ask" else "покупку"
                    self._send(f"eaten|{k}",
                               f"🍽 <b>{sym.removesuffix('USDT')}</b> съели плотность на {side}: "
                               f"{_fmt(d['price'])}, было {_money(d['notional'])} $")
        self.dens_prev = {k: v.get("s") for k, v in items.items()}

    def stats_updated(self, coins: dict[str, dict]) -> None:
        """Цена у хая или лоя дня."""
        for sym, st in coins.items():
            p, hi, lo = st.get("price"), st.get("day_hi"), st.get("day_lo")
            if not p or hi is None or lo is None:
                continue
            if (hi - p) / p * 100 <= NEAR_DAY:
                self._send(f"dayhi|{sym}|{time.strftime('%Y%m%d')}",
                           f"⬆️ <b>{sym.removesuffix('USDT')}</b> у хая дня: цена {_fmt(p)}, хай {_fmt(hi)}")
            elif (p - lo) / p * 100 <= NEAR_DAY:
                self._send(f"daylo|{sym}|{time.strftime('%Y%m%d')}",
                           f"⬇️ <b>{sym.removesuffix('USDT')}</b> у лоя дня: цена {_fmt(p)}, лой {_fmt(lo)}")

    def setups(self, live_dir: Path) -> None:
        """Новый сетап «можно торговать» — по тем же правилам, что на главной."""
        src, tn = live_dir / "structures.html", live_dir / "trend-now.json"
        if not src.exists() or not tn.exists():
            return
        mt = src.stat().st_mtime
        if mt == self.forms_mtime:
            return
        first = self.forms_mtime == 0.0
        self.forms_mtime = mt
        s = src.read_text(encoding="utf-8")
        m = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', s, re.S)
        if not m:
            return
        pairs = json.loads(m.group(1)).get("pairs", [])
        trends = json.loads(tn.read_text(encoding="utf-8")).get("coins", {})
        for p in pairs:
            tf = p["tf"]
            if tf not in ("5m", "15m", "1h"):
                continue
            tside = (trends.get(p["symbol"]) or {}).get("15m" if tf == "5m" else tf)
            for f in p.get("formations", []):
                if not f.get("triggered") or (f.get("age_candles") or 0) > 1:
                    continue
                if f["kind"] == "trendline_bounce" or f.get("entry") is None or f.get("stop") is None:
                    continue
                if f.get("direction") != tside or (f.get("rr") is not None and f["rr"] < 1):
                    continue
                key = f"setup|{p['symbol']}|{tf}|{f['kind']}|{f['direction']}|{f.get('ts')}"
                if key in self.forms_seen:
                    continue
                self.forms_seen.add(key)
                if first:                           # на старте старые сетапы не шлём
                    continue
                ms = f.get("measured") or {}
                exp = ms.get("exp_net")
                verdict = ("прошла замер" if ms.get("significant") and exp and exp > 0
                           else f"замер {exp:+.2f}R" if exp is not None else "замера нет")
                side = "ЛОНГ" if f["direction"] == "long" else "ШОРТ"
                self._send(key,
                           f"🎯 <b>{p['symbol'].removesuffix('USDT')}</b> {side} {tf}: {f.get('title') or f['kind']}\n"
                           f"вход {_fmt(f['entry'])} · стоп {_fmt(f['stop'])}"
                           + (f" · цель {_fmt(f['target'])}" if f.get("target") else "")
                           + (f" · R:R {f['rr']:.2f}" if f.get("rr") is not None else "")
                           + f"\n{verdict}. Это сигнал скринера, не совет.")
        if len(self.forms_seen) > 5000:
            self.forms_seen = set(list(self.forms_seen)[-2000:])
