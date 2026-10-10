"""Sole optional Telegram command poller, isolated from paper exit monitoring."""
import json,os,time
from tools.trade.screener_bot import ROOT
from tools.trade.run import load_env
from tools.trade import tg_control

if __name__=='__main__':
    load_env();root=ROOT/'data/trade'
    while True:
        ok=True
        try:
            if os.environ.get('TELEGRAM_CONTROL_ENABLED')=='1':tg_control.run(root)
        except Exception as exc:ok=False;print('Telegram control:',type(exc).__name__,flush=True)
        path=root/'control-status.json';tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(dict(updated_ms=int(time.time()*1000),enabled=os.environ.get('TELEGRAM_CONTROL_ENABLED')=='1',ok=ok)));os.replace(tmp,path);time.sleep(5)
