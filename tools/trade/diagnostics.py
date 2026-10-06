"""Publish per-profile setup results and refusal reasons from SQL journals."""
import json,html,os,time
from src.trade.atomic_store import database,read_journal
from src.trade.execution_stats import summarize
from tools.trade.screener_bot import ROOT

def publish(root=ROOT/'data/trade'):
    profiles={p.name:summarize(read_journal(p)) for p in sorted(root.glob('screener-*')) if database(p).exists()}
    data=dict(updated_ms=int(time.time()*1000),profiles=profiles);parts=['<h1>Разбор исполнения ботов</h1><p>Бумажная торговля. Исторические правила смешаны: это диагностика, не доказательство доходности новой версии. Ожидающий funding исключён из итогов. Повторы отказа считаются отдельно от уникальных ключей сигналов. Ключ без времени старого события может объединять разные сетапы.</p>']
    for name,stats in profiles.items():
        parts.append('<h2>'+html.escape(name)+'</h2><table><tr><th>Сетап / ТФ / сторона</th><th>Закрыто / ожидает funding</th><th>Net USDT</th><th>Плюсовых</th><th>Средний R</th><th>PF</th><th>Правила записаны</th></tr>')
        for r in stats['results']:
            label=html.escape(f"{r['formation']} / {r['tf']} / {r['side']}");avg='—' if r['avg_r'] is None else f"{r['avg_r']:+.2f}";pf='—' if r['profit_factor'] is None else f"{r['profit_factor']:.2f}"
            parts.append(f"<tr><td>{label}</td><td>{r['closed']} / {r['pending']}</td><td>{r['net']:+.4f}</td><td>{r['wins']}/{r['settled']}</td><td>{avg}</td><td>{pf}</td><td>{r['rules_known']}/{r['settled']}</td></tr>")
        parts.append('</table><h3>Причины пропусков</h3><table><tr><th>Причина</th><th>Записей отказа</th><th>Уникальных ключей</th></tr>')
        for r in stats['reasons']:parts.append(f"<tr><td>{html.escape(r['reason'])}</td><td>{r['retries']}</td><td>{r['unique_signals']}</td></tr>")
        parts.append(f"</table><p>Событий заполнения слотов: {stats['capacity_events']} (не число пропущенных монет).</p>")
    page='<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Разбор исполнения</title><style>body{background:#0d1420;color:#e3edf8;font:16px/1.6 system-ui;margin:24px}table{border-collapse:collapse;display:block;overflow:auto}td,th{padding:8px 14px;border-bottom:1px solid #34465e;text-align:left}h2{color:#82b6ff}</style>'+''.join(parts)+'<script src="/nav.js"></script>'
    for filename,value in [('execution-stats.json',json.dumps(data,ensure_ascii=False)),('execution-stats.html',page)]:
        path=root/filename;tmp=path.with_suffix('.tmp');tmp.write_text(value,encoding='utf-8');os.replace(tmp,path)
    return data

if __name__=='__main__':publish()
