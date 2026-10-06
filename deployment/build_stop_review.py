"""Build a sanitised, fixed-time audit from private SQL-derived snapshots."""
import json,html,statistics
from pathlib import Path
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1]
def date(ms):return datetime.fromtimestamp(ms/1000,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S МСК')
def f(v,n=3):return '—' if v is None else f'{v:.{n}f}'
def table(headers,rows):
 return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(str(c).replace('|','/') for c in row)+' |\n' for row in rows)
def main():
 before=json.loads((ROOT/'deployment/stop-review-before.json').read_text('utf-8'))
 after=json.loads((ROOT/'deployment/stop-review-after.json').read_text('utf-8'));data=after['audit']
 bp=[p for p in before['positions'] if not p['dust']];ps=[p for p in data['positions'] if not p['dust']]
 closed=sum(p['closed'] for p in before['profiles'].values());stops=sum(p['reasons'].get('stop',0) for p in before['profiles'].values())
 events=[e for e in after['events'] if e.get('reason')=='invalidation']
 caps={'5m':3,'15m':5,'1h':8,'4h':12}
 profile_rows=[[name,v['closed'],v['reasons'].get('stop',0),f(v['net']),', '.join(f'{k}: {n}' for k,n in v['reasons'].items())] for name,v in before['profiles'].items()]
 group_rows=[[g['profile'],g['kind'],g['tf'],g['side'],g['n'],g['stops'],f(g['net']),f(g['pf'])] for g in sorted(before['groups'],key=lambda g:g['net'])]
 def flags(p):
  out=[]
  if p['initial_stop_pct']>caps.get(p['tf'],12):out.append('старый стоп шире нового лимита')
  if p['stop_atr'] and p['stop_atr']>3:out.append('стоп >3 ATR в срезе аудита')
  if p['trend'] in ('long','short') and p['trend']!=p['side']:out.append('против текущего EMA-тренда')
  if not p['history_complete']:out.append('неполная история для MFE/MAE')
  if p['kind']=='volume_splash':out.append('старый вход только по объёму')
  return '; '.join(out) or 'нет перечисленных флагов'
 position_rows=[[p['profile'],p['symbol'],p['tf'],p['side'],p['kind'],f(p['entry'],6),f(p['stop'],6),f(p['target'],6) if p['target'] else 'трейлинг',f(p['initial_stop_pct'],2)+'%',f(p['stop_atr'],2),f(p['rr'],2),p['trend'] or '—',f(p['gross']),flags(p)] for p in sorted(ps,key=lambda p:p['initial_stop_pct'],reverse=True)]
 exit_rows=[[e['profile'],e['symbol'],e['tf'],f(e['exit'],6),f(e['pnl']-e.get('fee',0)-e.get('funding',0)),e.get('manual_reason','')] for e in events]
 intro=f'''# Проверка стопов, направлений и всех бумажных позиций

Исходный срез: {date(before['updated_ms'])}. Проверка после установки: {date(data['updated_ms'])}. Источники: семь SQL execution.db, журнал закрытий, локальные свечи и trend-now.json. Только бумажная торговля. Raw-журналы, ключи и учётные данные в Git не публикуются.

Коррекция расчёта: partial уже включён в close.pnl и не прибавляется повторно. В первоначальной версии managed ошибочно показан +10.0611USDT; корректный результат этого же среза −11.0671USDT. Состояние счетов и сделки не изменялись. Отдельная оценка новых входов: RULE-EVALUATION-20261006.md / https://vpn.markus.tw1.su/rule-evaluation.html.

До установки: {len(bp)} ненулевых открытых позиций, {closed} закрытий, {stops} выходов по стопу ({stops/closed*100:.1f}%). {sum(p['initial_stop_pct']>5 for p in bp)} открытых позиций имели исходный стоп шире 5%, {sum(p['initial_stop_pct']>10 for p in bp)} — шире 10%. После установки в проверенном срезе {len(ps)} позиций. Это разные тестовые профили; одинаковая монета в нескольких профилях не является независимым рыночным наблюдением.

## Что было неправильно

1. volume_splash входил самостоятельно, стоп брался за минимум/максимум большой импульсной свечи. FLUID: вход 2.207, стоп 1.857, расстояние 15.86%. Такое расстояние — не риск всего капитала; риск равен расстоянию до стопа, умноженному на qty, плюс расходы.
2. Для пробоя и смены структуры точный trigger_level не передавался по всему пути график → JSON → бот → позиция. Свежая цена могла оказаться обратно за уровнем до исполнения.
3. Выход по противоположному сигналу зависел от временного списка свежих формаций. Подтверждённая отмена старой идеи могла исчезнуть из списка, хотя позиция оставалась открытой.
4. Незаконченная текущая минута ошибочно считалась пробелом истории, когда курсор уже покрывал все закрытые минуты. Настоящие пробелы по-прежнему требуют восстановления.

## Что установлено

- Объёмный всплеск — кандидат для отдельного подтверждения пробоем/ретестом. Сам по себе он больше не открывает автоматическую позицию. Старые объёмные позиции не переименовываются задним числом.
- Для breakout/retest/structure_break сохраняется точный уровень. Перед входом и после расчёта исполнения проверяются текущая цена и свежая закрытая свеча: обе должны быть с нужной стороны уровня.
- Новые автоматические входы ограничены стопом 3% на 5м, 5% на 15м, 8% на 1ч, 12% на 4ч; при наличии истории также максимум 3 средних TR за 14 предшествующих свечей. Импульсная свеча в эту базу не входит. Это начальные ограничения бумажного испытания, их преимущество по доходности ещё не доказано. Широкий сетап пропускается; стоп не подтягивается произвольно ради входа. Явные ручные команды сохраняют заданные пользователем уровни.
- Отдельный screener-thesis.service каждые 10 секунд изучает локальные свечи открытых позиций вне блокировки исполнения. Два закрытия обратно за сохранённый уровень отменяют идею. Для старых позиций без уровня проверяется известный до выноса экстремум, возврат закрытием и слом границы свечи выноса следующей закрытой свечой. После восстановления уровня нужна новая подтверждённая потеря.
- Правило симметрично для long/short и связано с конкретной позицией/временем её открытия. Используется её собственный ТФ; младший шум не закрывает часовую позицию. Пробелы/устаревшие свечи не дают такого выхода. Исполнение требует свежей текущей цены; стоп и тейк имеют приоритет. Автоматического переворота позиции по одному стопу нет.
- Ограничения капитала/200 позиций, бумажный режим и накопленная история сохранены. Старые дальние стопы ниже перечислены отдельно: новое ограничение не меняет их задним числом. Временных выходов в новых правилах нет; старые timeout остаются в честном журнале.

## Подтверждённые выходы после установки

{table(['Профиль','Монета','ТФ','Цена выхода','Net USDT в журнале','Причина'],exit_rows)}
Цены — реальные бумажные исполнения на момент обработки. Не использовалась историческая цена в момент появления разворота. Funding может уточняться отдельным учётом. Нельзя приписывать исправлению экономию всей уже накопленной просадки.

## История профилей до установки

{table(['Профиль','Закрытий','Стопов','Net USDT','Все причины'],profile_rows)}
Частые стопы не означают автоматически убыток: alerts имел 38 стопов из 54 закрытий и положительный итог. Данные смешивают старые версии, разные размеры позиций, трейлинг и правила; сумма результатов профилей не является доходностью единого счёта. Net включает закрытый PnL, комиссии, funding и учтённые частичные закрытия. Малые выборки нельзя считать доказанным преимуществом.

## Все текущие позиции

{table(['Профиль','Монета','ТФ','Сторона','Сетап','Вход','Стоп','Цель','Исходный стоп','ATR×','R:R исходный','Тренд сейчас','Gross USDT','Флаги'],position_rows)}
ATR× в таблице — отдельный диагностический срез перед фактическим входом; он может включать импульс и отличается от новой доимпульсной базы для допуска. Тренд 5м берётся из доступного 15м EMA-контекста; это не утверждение, что есть независимый 5м тренд. Gross открытой позиции не учитывает комиссию выхода/funding. MFE/MAE в raw-аудите рассчитаны только по полностью последующим закрытым свечам: внутрисвечный путь и частичная свеча входа не восстановлены. Входной measured_r — агрегат класса по прошлой выборке, а не прогноз отдельной монеты.

## История по сетапам, направлениям и ТФ

{table(['Профиль','Сетап','ТФ','Сторона','N','Стопов','Net USDT','Profit factor'],group_rows)}

## Проверка и дальнейшая оценка

Локально 162 проверки торгового контура и 8 проверок потоков данных. На VPS 122 целевые проверки исполнения/подтверждения/восстановления и 8 потоковых. Сервисы после установки активны; журнал подтверждает четыре выхода по invalidation. В контрольной проверке 22:17 МСК 18 новых открытий после установки: ни одного volume_splash, превышения новых ограничений стопа или структурного входа без уровня. Recovery errors=[] после нескольких минутных границ; монитор ok, цикл около 2–3 секунд. SQL outbox подтверждает доставку четырёх фотографий выходов и отчёта в Telegram. HTTPS отчёта, ботов и структур проверен, ответы 200; визуальная проверка браузером не подтверждена из-за ранее установленной блокировки браузера.

Полезность новых правил нужно оценивать отдельно на новых сделках: доля стопов, net в R после расходов, MFE/MAE, запаздывание входа, пропуски и просадка. Сравнение по ТФ/сетапам и тренду, с отдельными часами сессий, должно идти на отложенной выборке. Не подгонять правила под FLUID и не повышать риск ради меньшего числа стопов.

Бэкап перед установкой: /opt/stop-review-backup-20261006T190737Z. В него сохранены изменяемые исходники и согласованные SQLite backup семи профилей. При отказе проверки скрипт восстанавливает код; историю торговли он не подменяет. Фактический статус свечей и уведомлений дополнен в deployment/HANDOFF.md.

## Материалы для проверки гипотез

[Binance: breakout](https://www.binance.com/en/academy/glossary/breakout): выход за уровень требует проверки, фитиль/объём сами по себе не гарантируют продолжение. [TradingView: ATR](https://www.tradingview.com/support/solutions/43000501823-average-true-range-atr/): TR учитывает диапазон и отклонение от предыдущего закрытия; ATR измеряет волатильность, не направление. В нашей контрольной базе применяется среднее TR, тогда как стандартный ATR TradingView использует RMA. [Tiger: scalping guide](https://blog.tiger.com/trading/scalping-trade-guide): учитывать препятствия, комиссии и зависимость размера позиции от расстояния до стопа; вести журнал с контекстом. Это источники гипотез, а не подтверждение прибыльности нашего бота.
'''
 for repo in ['wt-bots-srcdoc','screener-board']:
  (ROOT/repo/'docs/STOP-REVIEW-20261006.md').write_text(intro,'utf-8')
 # A static snapshot, with a filter for a full position ledger on small screens.
 def ht(headers,rows):return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'
 page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка стопов и позиций</title><style>body{margin:0;background:#101722;color:#dce6f5;font:15px/1.6 system-ui}main{max-width:1440px;margin:auto;padding:20px}a{color:#6dc5ff}h1{font-size:28px}h2{font-size:21px;margin-top:32px}.cards{display:flex;gap:12px;flex-wrap:wrap}.card{padding:16px;background:#1c2737;border-radius:12px}.card b{display:block;font-size:27px}.scroll{overflow:auto;max-height:70vh;border:1px solid #324256;border-radius:10px}table{border-collapse:collapse;width:100%;font-size:13px}th{position:sticky;top:0;background:#25344a;text-align:left;white-space:nowrap}td,th{padding:8px 12px;border-bottom:1px solid #2c394a}td{min-width:60px}tbody tr:hover{background:#25344a}input{padding:12px;background:#1c2737;color:white;border:1px solid #496681;border-radius:8px;margin:12px 0;width:min(90%,460px)}.note{color:#a4b7d0}</style><script src="/nav.js"></script><main>'''
 page+=f'<h1>Стопы, направления и позиции</h1><p class="note">Срез до: {date(before["updated_ms"])}. После установки: {date(data["updated_ms"])}. Фиксированный отчёт; текущие значения — на <a href="/bots.html">странице ботов</a>.</p>'
 page+='<div class="cards">'+''.join(f'<div class="card"><b>{v}</b>{label}</div>' for v,label in [(len(bp),'позиций до'),(closed,'закрытых сделок'),(stops,'стопов в истории'),(len(events),'подтверждённых выходов по отмене идеи')])+'</div>'
 page+='<p><a href="/rule-evaluation.html">Расчёт новых правил отдельно от старых сделок</a></p><p class="note">Исправлен отчётный двойной учёт partial: managed на исходном срезе −11.0671 USDT, вместо ошибочно показанных +10.0611. Балансы и торговые записи не менялись.</p><h2>Исправлено</h2><ul><li>Объём без отдельного пробоя/ретеста больше не открывает автоматический вход.</li><li>Сохраняется точный уровень; проверяются закрытие и цена исполнения.</li><li>Допуск стопа: 5м ≤3%, 15м ≤5%, 1ч ≤8%; при наличии истории ≤3 доимпульсных ATR. Широкий вход пропускается.</li><li>Выход при подтверждённой отмене идеи сопровождает каждую позицию независимо от списка свежих сигналов. Стоп/тейк приоритетны.</li><li>Исправлена ложная ошибка истории на границе минуты.</li></ul><p class="note">Старые позиции не переписаны и их стопы не сжаты. Пороговые ограничения пока экспериментальные; снижение числа стопов и рост доходности ещё не доказаны.</p>'
 page+='<h2>Выходы после установки</h2>'+ht(['Профиль','Монета','ТФ','Цена','Net USDT','Причина'],exit_rows)
 page+='<h2>Профили: история до изменения</h2>'+ht(['Профиль','Закрытий','Стопов','Net USDT','Все причины'],profile_rows)
 page+='<p class="note">История смешивает версии правил. Стопы не равны убытку стратегии; профили нельзя суммировать как один счёт. Funding уточняется отдельно.</p><h2>Все позиции после установки</h2><input id="filter" placeholder="Монета, профиль, таймфрейм, флаг" aria-label="Фильтр позиций"><div id="positions">'+ht(['Профиль','Монета','ТФ','Сторона','Сетап','Вход','Стоп','Цель','Исходный стоп','ATR×','R:R','Тренд','Gross USDT','Флаги'],position_rows)+'</div>'
 page+='<p class="note">ATR× здесь рассчитан перед фактическим входом и может включать импульс; новый допуск использует базу до сигнальной свечи. Для 5м показан доступный 15м контекст. Gross без расходов выхода/funding.</p><h2>Все группы истории</h2>'+ht(['Профиль','Сетап','ТФ','Сторона','N','Стопов','Net USDT','Profit factor'],group_rows)
 page+='<p><a href="/STOP-REVIEW-20261006.md">Полный разбор, проверки, ограничения и источники</a></p><p>Дальше: проверка новых сделок на отдельной выборке, net в R после расходов, MFE/MAE, задержка входа и результаты по ТФ, тренду и часам сессий. Прибыльность нового алгоритма пока не подтверждена.</p></main><script>document.getElementById("filter").addEventListener("input",function(){const q=this.value.toLocaleLowerCase();document.querySelectorAll("#positions tbody tr").forEach(r=>r.hidden=!r.textContent.toLocaleLowerCase().includes(q));});</script></html>'
 (ROOT/'deployment/published/position-review.html').write_text(page,'utf-8')
 print('Report positions',len(ps),'history groups',len(group_rows),'exits',len(events))
if __name__=='__main__':main()
