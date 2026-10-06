"""Reproducible arithmetic report; no trading commands or credentials."""
from pathlib import Path
import sys,json,html,statistics
from datetime import datetime,timezone,timedelta
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'wt-bots-srcdoc'))
from src.trade.rule_evaluation import evaluate,metrics,new_rules,net,usable
def f(v,n=4):return '—' if v is None else f'{v:.{n}f}'
def mdtable(headers,rows):return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+''.join('| '+' | '.join(map(str,row))+' |\n' for row in rows)
def main():
 raw=json.loads((ROOT/'deployment/rule-evaluation-private.json').read_text('utf-8'));d=evaluate(raw)
 (ROOT/'deployment/rule-evaluation-summary.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),'utf-8')
 stamp=datetime.fromtimestamp(d['updated_ms']/1000,timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S МСК')
 rows=[[k,p['new_opened'],p['new_live'],p['new']['closed'],f(p['new']['net']),p['new']['reasons'].get('stop',0),f(p['new']['win_rate'],1),f(p['new']['profit_factor'],3),f(p['new']['avg_r'],3),f(p['new_open_mark_after_estimated_exit_fees_before_funding'])] for k,p in d['profiles'].items()]
 riskrows=[[k,f(p['current_equity'],2),f(p['peak_drawdown_pct'],2)+'%',f(p['capital_used_pct'],2)+'%',f(p['stop_risk_pct'],2)+'%',f(p['new']['closed_curve_drawdown_usdt'])] for k,p in d['profiles'].items()]
 group_rows=[[g['profile'],g['kind'],g['tf'],g['side'],g['trend'],g['closed'],f(g['net']),f(g['avg_r'],3)] for g in d['groups']]
 legacyrows=[[r['profile'],r['symbol'],f(r['net']),r['pending']] for r in d['legacy_exits_after_install']]
 p=d['profiles']['screener-all-trend-tf'];m=p['new']
 # Correct earlier DERIVED summaries from canonical close records; keep a copy.
 oldpath=ROOT/'deployment/stop-review-before.json';before=json.loads(oldpath.read_text('utf-8'));backup=oldpath.with_name('stop-review-before-accounting-fix.json')
 if not backup.exists():backup.write_text(oldpath.read_text('utf-8'),'utf-8')
 for name,item in raw['profiles'].items():
  closed=[r for r in item['rows'] if r['kind']=='close' and usable(r) and r['ts']<=before['updated_ms']]
  before['profiles'][name]['net']=sum(net(r) for r in closed)
  for group in before['groups']:
   if group['profile']!=name:continue
   selected=[r for r in closed if (r.get('formation'),r['tf'],r['side'])==(group['kind'],group['tf'],group['side'])];vals=[net(r) for r in selected]
   loss=-sum(x for x in vals if x<0);group.update(net=sum(vals),wins=sum(x>0 for x in vals),pf=sum(x for x in vals if x>0)/loss if loss else None)
 oldpath.write_text(json.dumps(before,ensure_ascii=False,indent=2),'utf-8')
 afterpath=ROOT/'deployment/stop-review-after.json';after=json.loads(afterpath.read_text('utf-8'))
 for e in after['events']:
  updated=next((r for r in raw['profiles'][e['profile']]['rows'] if r.get('event_id')==e.get('event_id')),None)
  if updated:e.update(updated)
 afterpath.write_text(json.dumps(after,ensure_ascii=False,indent=2),'utf-8')
 note='''# Оценка новых правил и расчёт результатов

Срез: STAMP. Новые позиции определены по сохранённым полям новой версии правил (prior_atr/trigger_level) и времени входа после установки. Старый вход, закрывшийся позже, не относится к новой стратегии. Read-only SQLite snapshot каждого профиля: состояние и журнал прочитаны в одной транзакции. Между профилями небольшая разница времени чтения; это разные бумажные счета.

## Новые входы

TABLE

Net закрытых = close.pnl − close.fee − close.funding. Частичные закрытия уже включены в close.pnl; повторно partial не прибавляется. Незавершённый funding выделен отдельно; в этом срезе для восьми новых закрытий он рассчитан. Открытый результат рассчитан по mark, с комиссией входа, частичными результатами и оценочной комиссией выхода по ставке фактического входа. Это не исполнение: проскальзывание и funding открытых позиций не восстановлены. Большинство новых входов ещё открыто; отбор только быстро закрывшихся сделок создаёт смещение.

## Вывод по результату

TREND

Managed: одна новая закрытая сделка EDUUSDT 15m long, net −0.127910 USDT, −1.040R. Остальные 11 новых позиций ещё открыты; их оценка после комиссии выхода, до funding +0.1183 USDT. Одна потеря не доказывает убыточность стратегии. Alerts: один новый вход ещё открыт; у остальных четырёх профилей новых входов в срезе нет. Вместе это 28 открытий и восемь закрытий в семи тестовых профилях, а не доходность одного портфеля. Нет основания объявлять новые правила прибыльными.

## По сетапам и направлению

GROUPS

Пять новых шортов all-trend-tf закрылись в плюс; два лонга 15m — в минус. Ещё один лонг managed вышел по стопу. Это несколько сделок одного рыночного окна; UMA повторяется на разных ТФ. Нельзя объявлять шорт лучшим режимом или отключать лонги по этой выборке. Для текущих слабых целей важнее соотношение среднего выигрыша и среднего проигрыша, чем число зелёных сделок.

## Риск и капитал

RISKS

Просадка от пика относится ко всему профилю, включая старые сделки; её нельзя присвоить только новым правилам. Последняя колонка — максимальное падение накопленного результата новых ЗАКРЫТЫХ сделок от его предыдущего пика, не полная внутридневная просадка счёта. Стоп-риск — сумма потерь от mark до текущих стопов / equity, до расходов и неблагоприятных гэпов; это модель одновременного достижения стопов, не гарантия ограничения потери.

В all занято 99.60% equity: старые позиции открыты до лимита 80%; сейчас новых входов там нет. Новые входы проверяют остаток бюджета и не уменьшают старые позиции автоматически. На условных 1000USDT: разрешённый общий номинал при 80% — 800USDT. Код size ограничивает одну новую позицию примерно equity/200=5USDT; при таком размере бюджет обычно заполнится около 160 позиций, а не 200. 200 — потолок количества. При номинале5USDT и стопе1% ценовой риск0.05USDT; это0.005% от1000USDT, а не1% счёта. При комиссии0.05% на каждой стороне круг стоит приблизительно0.005USDT; для стопа1% это0.1R до проскальзывания. Это пример с фактической ставкой новых входов, не новый лимит риска.

## Старые выходы при отмене идеи

LEGACY

Это убытки старых входов, после уточнения комиссии и funding. Они не записаны в метрики новых входов и не пересчитаны по историческим ценам.

## Исправление прежнего отчёта

В position_audit частичный результат ошибочно прибавлялся повторно. На прежнем срезе 22:07:38 результат managed был показан +10.0611USDT; корректное значение −11.0671USDT. Разница21.1282USDT — повторный учёт partial, а не новый торговый убыток. SQL состояние/cash и формула торговли были правильными; исправлены только отчётные агрегаты. Страница position-review и MD обновлены с явной пометкой. Три теста проверяют отсутствие двойного счёта, разделение когорт и расчёт просадки/порога безубыточности.

## Что проверять дальше

Результаты считать после полного закрытия каждого входа; формировать отдельные когорты по версии, сетапу, ТФ, тренду и сессии. Отдельно испытать фильтр минимального net R:R перед входом: в all-trend-tf сейчас min_entry_rr=0, поэтому проходят очень малые цели. Порог безубыточности по текущим средним доходу/убытку — диагностический, он нестабилен на семи сделках. В этом расчёте конфигурацию входов и размеры позиций не меняли.

Для оценки доли выигрышей с точностью около ±5 процентных пунктов при95% доверии в худшем биномиальном случае потребуется около385 независимых исходов (1.96²×0.25/0.05²). Это не доказывает положительную доходность, а рыночная зависимость уменьшает эффективную выборку. До накопления разных режимов оставлять оценку предварительной, без экстраполяции прибыли на месяц/год. MFE/MAE требуют сохранённого пути внутри сделки; из одной итоговой свечи их точно восстановить нельзя.
'''
 trend=f"Скринер + тренд: {m['closed']} закрытий, пять целей и два стопа. Win rate {f(m['win_rate'],2)}%; net {f(m['net'],6)}USDT после комиссий {f(m['fees'],6)}USDT и funding {f(m['funding'],6)}USDT. Profit factor {f(m['profit_factor'],4)} — почти безубыток. Средний выигрыш {f(m['avg_win'],6)}USDT, средний проигрыш {f(m['avg_loss'],6)}USDT. Требуемая доля выигрышей для безубытка при этих средних = loss/(win+loss) = {f(m['break_even_win_rate'],2)}%. Наблюдаемая доля лишь на {f(m['win_rate']-m['break_even_win_rate'],2)} п.п. выше. Среднее нормированное net R {f(m['avg_r'],4)}R: слабый результат; небольшой плюс в деньгах не означает плюс при одинаковом риске каждой сделки. Условный 95% интервал доли выигрышей {f(m['win_rate_ci95'][0],1)}–{f(m['win_rate_ci95'][1],1)}% включает порог безубытка; зависимость сделок делает простую биномиальную оценку ещё менее убедительной. Открытые восемь новых позиций после оценочной комиссии выхода, до funding: {f(p['new_open_mark_after_estimated_exit_fees_before_funding'])}USDT."
 for a,b in {'STAMP':stamp,'TABLE':mdtable(['Профиль','Входов','Открыто','Закрыто','Net USDT','Стопов','Win %','PF','Среднее R','Открытый mark net*'],rows),'TREND':trend,'GROUPS':mdtable(['Профиль','Сетап','ТФ','Сторона','Тренд входа','N','Net USDT','Среднее R'],group_rows),'RISKS':mdtable(['Профиль','Equity USDT','Просадка от пика','Занято капитала','Стоп-риск от mark','DD новых закрытий USDT'],riskrows),'LEGACY':mdtable(['Профиль','Монета','Net USDT','Funding pending'],legacyrows)}.items():note=note.replace(a,b)
 for repo in ['wt-bots-srcdoc','screener-board']:(ROOT/repo/'docs/RULE-EVALUATION-20261006.md').write_text(note,'utf-8')
 # Safe HTML rendering of our plain Markdown report (tables, paragraphs, headings).
 parts=[];table=[]
 def flush():
  if not table:return
  heads=table[0];body=table[2:];parts.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in heads)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(x)+'</td>' for x in row)+'</tr>' for row in body)+'</tbody></table></div>');table.clear()
 for line in note.splitlines():
  if line.startswith('|'):table.append([x.strip() for x in line.strip('|').split('|')]);continue
  flush()
  if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
  elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
  elif line.strip():parts.append('<p>'+html.escape(line)+'</p>')
 flush()
 page='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Расчёт новых правил</title><script src="/nav.js"></script><style>body{margin:0;background:#111925;color:#dce6f5;font:15px/1.65 system-ui}main{max-width:1280px;margin:auto;padding:20px}a{color:#6dc5ff}h1{font-size:27px}h2{margin-top:32px;font-size:21px}.scroll{overflow:auto;border:1px solid #324256;border-radius:10px}table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:9px;border-bottom:1px solid #324256;text-align:left}th{background:#25344a;white-space:nowrap}tbody tr:hover{background:#25344a}</style><main><p><a href="/position-review.html">Все позиции и исправления</a> · <a href="/RULE-EVALUATION-20261006.md">Исходный отчёт</a></p>'+''.join(parts)+'</main></html>'
 (ROOT/'deployment/published/rule-evaluation.html').write_text(page,'utf-8')
 for repo in ['wt-bots-srcdoc','screener-board']:(ROOT/repo/'docs/rule-evaluation.html').write_text(page,'utf-8')
 print('snapshot',stamp,'new openings',sum(p['new_opened'] for p in d['profiles'].values()),'closed',sum(p['new']['closed'] for p in d['profiles'].values()))
if __name__=='__main__':main()
