"""Publish per-profile setup results and refusal reasons from SQL journals."""
import json,html,os,time,datetime
from src.trade.atomic_store import database,read_journal,read_state
from src.trade.execution_stats import summarize,cohort
from tools.trade.screener_bot import ROOT

def publish(root=ROOT/'data/trade'):
    journals={p.name:read_journal(p) for p in sorted(root.glob('screener-*')) if database(p).exists()}
    profiles={name:summarize(rows) for name,rows in journals.items()}
    since=int(os.environ.get('SCREENER_STATS_FROM_MS','1791307067000'))
    recent={name:summarize(cohort(rows,since)) for name,rows in journals.items()}
    data=dict(updated_ms=int(time.time()*1000),profiles=profiles,recent_from_ms=since,recent_profiles=recent);parts=['<h1>Разбор исполнения ботов</h1><p>Бумажная торговля. Исторические правила смешаны: это диагностика, не доказательство доходности новой версии. Ожидающий funding исключён из итогов. Повторы отказа считаются отдельно от уникальных ключей сигналов. Ключ без времени старого события может объединять разные сетапы.</p>']
    since_label=datetime.datetime.fromtimestamp(since/1000,datetime.timezone(datetime.timedelta(hours=3))).strftime('%d.%m.%Y %H:%M:%S')
    parts.append('<h2>Новые входы с '+since_label+' МСК</h2><p>Отдельная выборка после установки восстановления истории. Старые позиции, закрытые позднее, сюда не входят. Система исполнения обновлялась в течение дня; это не отдельная торговая стратегия.</p><table><tr><th>Профиль</th><th>Закрыто новых / ждёт funding</th><th>Net USDT</th><th>Отказов / уникальных ключей по причинам</th></tr>')
    for name,stats in recent.items():
        reasons='; '.join(f"{r['reason']}: {r['retries']} / {r['unique_signals']}" for r in stats['reasons'][:5]) or 'нет'
        parts.append(f"<tr><td>{html.escape(name)}</td><td>{sum(r['closed'] for r in stats['results'])} / {sum(r['pending'] for r in stats['results'])}</td><td>{sum(r['net'] for r in stats['results']):+.4f}</td><td>{html.escape(reasons)}</td></tr>")
    parts.append('</table><h2>Капитал и открытые позиции сейчас</h2><p>Каждый профиль имеет отдельный виртуальный капитал. Старые крупные позиции могут занимать лимит 80% даже после увеличения числа слотов. Размер новых входов ограничен 0,5% equity при 200 слотах.</p><table><tr><th>Профиль</th><th>Позиций / микропозиций</th><th>Equity USDT</th><th>Номинал позиций USDT</th><th>Использование капитала</th></tr>')
    data['capital']={}
    for name in journals:
        state=read_state(root/name) or {};positions=list(state.get('positions',{}).values());used=sum(p['qty']*(p.get('mark') or p['entry']) for p in positions)
        equity=state.get('cash',0)+sum((1 if p['side']=='long' else -1)*p['qty']*((p.get('mark') or p['entry'])-p['entry']) for p in positions)
        dust=sum(p['qty']*p['entry']<=1e-6 for p in positions);util=used/equity if equity>0 else None
        data['capital'][name]=dict(positions=len(positions),dust=dust,equity=equity,notional=used,utilization=util)
        label='—' if util is None else f'{util:.1%}'
        parts.append(f'<tr><td>{html.escape(name)}</td><td>{len(positions)} / {dust}</td><td>{equity:.2f}</td><td>{used:.2f}</td><td>{label}</td></tr>')
    parts.append('</table><p>Закрытые микросделки с номиналом ≤ 0,000001 USDT исключены из PF, среднего R и числа прибыльных. Исходный журнал сохранён.</p><h2>Вся история</h2>')
    for name,stats in profiles.items():
        parts.append('<h2>'+html.escape(name)+'</h2><table><tr><th>Сетап / ТФ / сторона</th><th>Закрыто / ожидает funding / микро</th><th>Net USDT</th><th>Плюсовых</th><th>Средний R</th><th>PF</th><th>Правила записаны</th></tr>')
        for r in stats['results']:
            label=html.escape(f"{r['formation']} / {r['tf']} / {r['side']}");avg='—' if r['avg_r'] is None else f"{r['avg_r']:+.2f}";pf='—' if r['profit_factor'] is None else f"{r['profit_factor']:.2f}"
            parts.append(f"<tr><td>{label}</td><td>{r['closed']} / {r['pending']} / {r['dust']}</td><td>{r['net']:+.4f}</td><td>{r['wins']}/{r['settled']}</td><td>{avg}</td><td>{pf}</td><td>{r['rules_known']}/{r['settled']}</td></tr>")
        parts.append('</table><h3>Часы и сессии на входе</h3><p>Одна сделка может относиться к нескольким меткам: строки нельзя суммировать. Сессии — временной контекст, а не сигнал; биржевые праздники не проверены.</p><table><tr><th>Контекст</th><th>Закрыто</th><th>Net USDT</th></tr>')
        for r in stats['contexts']:parts.append(f"<tr><td>{html.escape(r['context'])}</td><td>{r['n']}</td><td>{r['net']:+.4f}</td></tr>")
        parts.append('</table><h3>Причины пропусков</h3><table><tr><th>Причина</th><th>Записей отказа</th><th>Уникальных ключей</th></tr>')
        for r in stats['reasons']:parts.append(f"<tr><td>{html.escape(r['reason'])}</td><td>{r['retries']}</td><td>{r['unique_signals']}</td></tr>")
        parts.append(f"</table><p>Событий заполнения слотов: {stats['capacity_events']} (не число пропущенных монет).</p>")
    page='<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Разбор исполнения</title><style>body{background:#0d1420;color:#e3edf8;font:16px/1.6 system-ui;margin:24px}table{border-collapse:collapse;display:block;overflow:auto}td,th{padding:8px 14px;border-bottom:1px solid #34465e;text-align:left}h2{color:#82b6ff}</style>'+''.join(parts)+'<script src="/nav.js"></script>'
    for filename,value in [('execution-stats.json',json.dumps(data,ensure_ascii=False)),('execution-stats.html',page)]:
        path=root/filename;tmp=path.with_suffix('.tmp');tmp.write_text(value,encoding='utf-8');os.replace(tmp,path)
    return data

if __name__=='__main__':publish()
