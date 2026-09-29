"""Скринер: сегодняшние формации, ранжированные по чистой ожидаемости.

Что делает и почему именно так. Детекторы (`src/analysis/formations.py`) находят
формации на последних свечах — это ответ на вопрос «что сейчас происходит».
Вопрос «стоит ли это брать» они не решают и решать не могут: формация,
найденная по правилам, сама по себе не значит, что она прибыльна. Поэтому
рядом с каждой формацией стоит измеренный на архиве результат её типа
(таблица `formation_stats`, считается `tools/live/refresh.py`) и издержки
этой конкретной сделки в единицах риска (`src/backtest/costs.py`).

Ранжирование идёт по чистой ожидаемости, а не по уверенности детектора.
Уверенность — это доля сошедшихся условий разбора, она ничего не говорит о
результате; измеренная ожидаемость говорит о нём прямо. Сигналы, по которым
измерения нет или сделок в нём мало, помечены явно: пустая клетка вместо
числа читалась бы как «неплохо».

Живой стакан добавляет к сигналу то, чего в свечах нет: спред и стоящий объём
в полосе вокруг цены. Это не фильтр и не повод взять сигнал — это условие
исполнения: на широком спреде и тонкой полосе цена входа недостижима.
"""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from src.analysis.formations import Formation, detect_all
from src.backtest.costs import Costs
from src.backtest.expectancy import STEP_BY_TF
from src.backtest.walk import HISTORY
from src.data.market import Candle
from src.storage import db

# Время в отчёте и на странице — московское: МСК это UTC+3 без перехода на
# летнее, то есть фиксированный сдвиг, а не местная зона машины.
MSK = timezone(timedelta(hours=3))

# Минимум сделок, при котором измерение считается опорой. Порог грубый и
# намеренно высокий: на десятке сделок средний R гуляет на единицы, и
# ранжировать по такому числу — то же, что ранжировать по случаю.
MIN_TRADES = 30

# Сколько свечей давать детектору. Столько же, сколько в прогоне по архиву
# (HISTORY в src/backtest/walk.py): иначе живой скринер находил бы не то, что
# измерялось, и числа из таблицы к сигналу не относились бы.
WINDOW = 300


@dataclass
class Row:
    """Сигнал вместе с тем, что о нём известно помимо детектора."""
    kind: str
    title: str
    tf: str
    symbol: str
    exchange: str
    direction: str
    entry: float
    stop: float
    target: float
    rr: float
    stop_pct: float
    confidence: float
    triggered: bool
    age_candles: int
    reasons: list[str] = field(default_factory=list)
    measured: dict | None = None      # строка formation_stats
    cost_now: float = 0.0             # издержки этой сделки в R, без фандинга
    mid: float = 0.0                  # середина стакана на момент сбора
    spread_bps: float = 0.0
    imbalance: float = 0.0
    band_usdt: float = 0.0            # объём на стороне входа в полосе 10 б.п.

    @property
    def exp_net(self) -> float | None:
        m = self.measured
        if not m or m["n"] < MIN_TRADES:
            return None
        return m["exp_net"]

    @property
    def rank_key(self) -> tuple:
        e = self.exp_net
        # измеренные сигналы идут первыми и по убыванию ожидаемости;
        # неизмеренные — следом, по уверенности детектора, и это не оценка
        return (0, -e, -self.confidence) if e is not None \
            else (1, 0.0, -self.confidence)


def _candles(conn, symbol: str, tf: str, limit: int = WINDOW) -> list[Candle]:
    rows = db.latest_candles(conn, limit, symbol=symbol, timeframe=tf)
    return [Candle(r["open_ts"], r["open"], r["high"], r["low"], r["close"],
                   r["volume"], r["quote_volume"], r["trades"])
            for r in reversed(rows)]


def _books(conn) -> dict[str, dict]:
    """Последний снимок стакана по каждой монете, одним запросом."""
    out: dict[str, dict] = {}
    for row in db.latest_book_snapshots(conn, 10_000):
        out.setdefault(row["symbol"], dict(row))
    return out


def signals(conn, tfs: tuple[str, ...], exchange: str = "binance",
            min_candles: int = 60) -> tuple[list[Formation], list[str]]:
    """Формации по всем монетам базы. Второе — что не удалось разобрать."""
    found: list[Formation] = []
    notes: list[str] = []
    symbols = db.symbols_present(conn)
    btc_by_tf: dict[str, list[Candle]] = {}
    for sym in symbols:
        for tf in tfs:
            cs = _candles(conn, sym, tf)
            if len(cs) < min_candles:
                notes.append(f"{sym} {tf}: свечей {len(cs)}, разбор пропущен "
                             f"(нужно {min_candles})")
                continue
            btc = None
            if sym != "BTCUSDT":
                if tf not in btc_by_tf:
                    btc_by_tf[tf] = _candles(conn, "BTCUSDT", tf)
                btc = btc_by_tf[tf] or None
            try:
                found.extend(detect_all(cs, tf, sym, exchange, btc=btc))
            except Exception as exc:                        # noqa: BLE001
                notes.append(f"{sym} {tf}: разбор упал — {type(exc).__name__}")
    return found, notes


def rank(found: list[Formation], stats: dict, conn, costs: Costs,
         exchange: str = "binance") -> list[Row]:
    """Собрать строки отчёта и отсортировать по чистой ожидаемости."""
    rows: list[Row] = []
    books = _books(conn)
    for f in found:
        if not f.targets:
            continue
        risk = abs(f.entry - f.stop)
        if risk <= 0 or f.entry <= 0:
            continue
        # издержки этой сделки: круговая комиссия и проскальзывание, делённые
        # на расстояние до стопа. Фандинга здесь нет — время удержания ещё
        # неизвестно; он учтён в измеренных числах по формации.
        cost_now = costs.round_trip * f.entry / risk
        book = books.get(f.symbol) or {}
        side_key = "bid_10bps" if f.direction == "long" else "ask_10bps"
        rows.append(Row(
            kind=f.kind, title=f.title, tf=f.tf, symbol=f.symbol,
            exchange=exchange, direction=f.direction, entry=f.entry,
            stop=f.stop, target=f.targets[0], rr=f.rr,
            stop_pct=risk / f.entry * 100, confidence=f.confidence,
            triggered=f.triggered, age_candles=f.age_candles,
            reasons=list(f.reasons),
            measured=stats.get((f.kind, f.tf)),
            cost_now=cost_now,
            mid=book.get("mid", 0.0), spread_bps=book.get("spread_bps", 0.0),
            imbalance=book.get("imbalance", 0.0),
            band_usdt=book.get(side_key, 0.0)))
    rows.sort(key=lambda r: r.rank_key)
    return rows


# Оговорки, без которых числа читаются как больше, чем они есть. Собираются
# функцией, а не лежат абзацем в тексте: их обязательно надо показать и на
# странице, а два текста про одно и то же разошлись бы.
def _caveats(meta: dict) -> tuple[str, ...]:
    steps = ", ".join(f"{tf} — {STEP_BY_TF.get(tf, 5)}" for tf in
                      (meta.get("tfs") or "").split(", ") if tf)
    return (
        f"Измерение считает формацию на всём префиксе свечей: детектор получает "
        f"историю от {HISTORY} свечей и до конца архива, а срезов за окно "
        f"берётся по одному на столько свечей ({steps}). Скринер ищет ту же "
        f"формацию на последних {WINDOW} свечах базы — это не то же самое "
        f"измерение, совпадает только длина истории у детектора.",
        f"«n» — сделки по всем монетам измерения ({meta.get('scope') or '—'}) "
        f"за окно измерения, а не по монете и не по сигналу этой строки: одна "
        f"и та же формация на разных монетах идёт в одно число.",
        f"Меньше {MIN_TRADES} сделок в измерении — строка показана, но опорой "
        f"не считается: на десятке сделок средний R гуляет на единицы.",
    )


def _table_note(stats: dict, rows: list[Row]) -> str:
    """Строка про саму таблицу измерений, а не про сегодняшние сигналы.

    Два числа «из 25» легко совпадают и читаются как одно: сигналов с
    измерением и формаций в таблице. Это разные наборы, и путать их нельзя —
    в таблице плюсовые формации есть, а среди сегодняшних сигналов их может
    не быть вовсе.
    """
    anchors = [m for m in stats.values() if m["n"] >= MIN_TRADES]
    pos = sum(1 for m in anchors if m["exp_net"] > 0)
    hit = {(r.kind, r.tf) for r in rows if r.exp_net is not None}
    pos_keys = {(k, tf) for (k, tf), m in stats.items()
                if m["n"] >= MIN_TRADES and m["exp_net"] > 0}
    tail = "сегодня ни одна из них не сработала" if not (pos_keys & hit) \
        else f"из них сегодня сработало {len(pos_keys & hit)}"
    return (f"в таблице измерений формаций {len(stats)}, опора (n ≥ "
            f"{MIN_TRADES}) у {len(anchors)}, в плюсе {pos} — {tail}")


def render_text(rows: list[Row], notes: list[str], stats: dict,
                meta: dict) -> str:
    out: list[str] = []
    measured = [r for r in rows if r.exp_net is not None]
    neg = sum(1 for r in measured if r.exp_net < 0)
    out.append(f"сигналов {len(rows)}, из них с измерением {len(measured)}")
    if measured:
        out.append(f"у этих сигналов чистая ожидаемость положительна у "
                   f"{len(measured) - neg} из {len(measured)}, "
                   f"отрицательна у {neg}")
    out.append(_table_note(stats, rows))
    out.append("")
    out.append(f"{'формация':22} {'ТФ':4} {'монета':9} {'стор':5} {'вход':>10} "
               f"{'стоп %':>7} {'R:R':>5} {'изд. R':>7} {'n':>5} "
               f"{'R net':>8} {'увер':>5} {'спред':>6}")
    for r in rows:
        e = r.exp_net
        m = r.measured
        out.append(
            f"{r.kind:22} {r.tf:4} {r.symbol:9} {r.direction:5} "
            f"{r.entry:10.6g} {r.stop_pct:7.2f} {r.rr:5.2f} {r.cost_now:7.3f} "
            f"{(m['n'] if m else 0):5} "
            f"{('—' if e is None else f'{e:+.3f}'):>8} {r.confidence:5.2f} "
            f"{r.spread_bps:6.2f}")
    if notes:
        out.append("")
        out.append("не разобрано:")
        out.extend(f"  {n}" for n in notes)
    out.append("")
    out.append("«изд. R» — издержки этой сделки (комиссия и проскальзывание, "
               "делённые на расстояние до стопа); фандинг в них не входит и "
               "учтён в измеренном «R net». «—» вместо R net — измерение "
               f"отсутствует или сделано меньше чем на {MIN_TRADES} сделках.")
    out.append("")
    out.append("оговорки:")
    out.extend(f"  · {c}" for c in _caveats(meta))
    return "\n".join(out)


def to_json(rows: list[Row], notes: list[str], meta: dict) -> str:
    return json.dumps({
        "meta": meta,
        "signals": [{**{k: v for k, v in asdict(r).items()},
                     "exp_net": r.exp_net} for r in rows],
        "notes": notes,
    }, ensure_ascii=False, indent=2)


def render_html(rows: list[Row], notes: list[str], meta: dict,
                stats: dict) -> str:
    """Страница отчёта. Текст экранируется: монеты и причины — внешние данные."""
    e = html.escape
    measured = [r for r in rows if r.exp_net is not None]
    body: list[str] = []
    for r in rows:
        m = r.measured
        net = r.exp_net
        cls = "plain" if net is None else ("good" if net > 0 else "bad")
        net_txt = "не измерено" if net is None else f"{net:+.3f} R"
        if net is None and m:
            net_txt = f"мало сделок (n={m['n']})"
        body.append(f"""
      <tr class="{cls}" title="{e('; '.join(r.reasons))}">
        <td>{e(r.kind)}<div class="sub">{e(r.title)}</div></td>
        <td>{e(r.tf)}</td>
        <td><b>{e(r.symbol)}</b></td>
        <td class="{'long' if r.direction == 'long' else 'short'}">{e(r.direction)}</td>
        <td class="num">{r.entry:.6g}</td>
        <td class="num">{r.stop:.6g}<div class="sub">{r.stop_pct:.2f} %</div></td>
        <td class="num">{r.target:.6g}<div class="sub">R:R {r.rr:.2f}</div></td>
        <td class="num">{r.cost_now:.3f} R</td>
        <td class="num">{net_txt}<div class="sub">{'n=' + str(m['n']) if m else '—'}</div></td>
        <td class="num">{r.confidence:.2f}</td>
        <td class="num">{r.spread_bps:.2f}<div class="sub">{r.band_usdt:,.0f} USDT</div></td>
      </tr>""")
    neg = sum(1 for r in measured if r.exp_net < 0)
    verdict = (f"ни у одного из {len(measured)} сегодняшних сигналов с "
               f"измерением формация не в плюсе"
               if measured and neg == len(measured)
               else f"из {len(measured)} сегодняшних сигналов с измерением "
                    f"в плюсе {len(measured) - neg}")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Скринер</title>
<style>
  :root {{ --bg:#12141a; --fg:#e6e8ee; --dim:#8b93a7; --line:#252a36;
           --good:#3ddc97; --bad:#ff6b6b; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
          font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }}
  h1 {{ font-size:18px; margin:0 0 4px; }}
  .meta {{ color:var(--dim); margin-bottom:16px; }}
  .verdict {{ padding:10px 12px; border-left:3px solid var(--bad);
              background:#1a1d26; margin-bottom:18px; }}
  table {{ border-collapse:collapse; width:100%; }}
  th,td {{ text-align:left; padding:7px 9px; border-bottom:1px solid var(--line);
           vertical-align:top; }}
  th {{ color:var(--dim); font-weight:400; position:sticky; top:0;
        background:var(--bg); }}
  td.num {{ text-align:right; white-space:nowrap; }}
  .sub {{ color:var(--dim); font-size:11px; }}
  .long {{ color:var(--good); }} .short {{ color:var(--bad); }}
  tr.good td:nth-child(9) {{ color:var(--good); }}
  tr.bad  td:nth-child(9) {{ color:var(--bad); }}
  .notes {{ margin-top:18px; color:var(--dim); white-space:pre-wrap; }}
  .caveat {{ margin-top:18px; padding:10px 12px; border-left:3px solid var(--line);
             background:#161922; color:var(--dim); }}
  .caveat b {{ color:var(--fg); font-weight:400; }}
  .caveat ul {{ margin:6px 0 0; padding-left:18px; }}
  .caveat li {{ margin-bottom:4px; }}
  .wrap {{ overflow-x:auto; }}
</style></head><body>
<h1>Скринер: сигналы и их измеренный результат</h1>
<div class="meta">{e(meta['when'])} МСК · монет {meta['symbols']} ·
  таймфреймы {e(meta['tfs'])} · измерение от {e(meta['measured_on'] or '—')}
  ({e(meta['scope'] or '—')})</div>
<div class="verdict">{e(verdict)}: измеренных сигналов {len(measured)} из
  {len(rows)}. {e(_table_note(stats, rows))}. Ранжирование — по чистой
  ожидаемости, а не по уверенности детектора.</div>
<div class="wrap"><table>
  <thead><tr><th>формация</th><th>ТФ</th><th>монета</th><th>сторона</th>
  <th>вход</th><th>стоп</th><th>цель</th><th>издержки сделки</th>
  <th>R net (измерено)</th><th>уверенность</th><th>спред / объём</th></tr></thead>
  <tbody>{''.join(body)}</tbody>
</table></div>
<div class="caveat"><b>Чего эти числа не значат</b>
<ul>{''.join(f'<li>{e(c)}</li>' for c in _caveats(meta))}</ul></div>
<div class="notes">{e(chr(10).join(notes)) if notes else ''}</div>
</body></html>
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None)
    p.add_argument("--tfs", nargs="+", default=["5m", "15m", "1h"])
    p.add_argument("--exchange", default="binance")
    p.add_argument("--fee", type=float, default=0.0004)
    p.add_argument("--slippage", type=float, default=0.0001)
    p.add_argument("--json", dest="json_path", default=None)
    p.add_argument("--html", dest="html_path", default=None)
    a = p.parse_args()

    conn = db.connect(a.db)
    stats = db.load_formation_stats(conn)
    costs = Costs(taker_fee=a.fee, slippage=a.slippage)
    found, notes = signals(conn, tuple(a.tfs), a.exchange)
    rows = rank(found, stats, conn, costs, a.exchange)

    measured_on = next((m["measured_on"] for m in stats.values()), None)
    scope = next((m["symbol_scope"] for m in stats.values()), None)
    meta = {
        "when": datetime.now(MSK).strftime("%Y-%m-%d %H:%M"),
        "symbols": len(db.symbols_present(conn)),
        "tfs": ", ".join(a.tfs),
        "measured_on": measured_on,
        "scope": scope,
    }
    print(render_text(rows, notes, stats, meta))
    if a.json_path:
        with open(a.json_path, "w", encoding="utf-8") as fh:
            fh.write(to_json(rows, notes, meta))
        print(f"\nJSON: {a.json_path}")
    if a.html_path:
        with open(a.html_path, "w", encoding="utf-8") as fh:
            fh.write(render_html(rows, notes, meta, stats))
        print(f"страница: {a.html_path}")


if __name__ == "__main__":
    main()
