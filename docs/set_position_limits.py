"""Set paper profile capacity without altering ledger or risk controls."""
import json,time,shlex
from deploy_server import connect,run,put,HOST

def main():
 s=connect(HOST,'root');path='/opt/crypto-trade/data/trade/screener-profiles.json'
 stamp=time.strftime('%Y%m%d-%H%M%S');old=None;changed=False
 try:
  run(s,'systemctl stop screener-bots.timer screener-bots.service',quiet=True)
  try:
   old=run(s,'cat '+path,quiet=True);profiles=json.loads(old)
   assert len(profiles)==7
   for args in profiles:
    i=args.index('--max-open');args[i+1]='200'
    if '--max-side' in args:args[args.index('--max-side')+1]='200'
    assert args[args.index('--capital-fraction')+1]=='0.8'
    assert args[args.index('--max-drawdown')+1]=='0.25'
   run(s,'cp -a '+path+' '+path+'.before-limit-'+stamp,quiet=True)
   tmp=path+'.limit-tmp';put(s,tmp,json.dumps(profiles,ensure_ascii=False,indent=2)+'\n',0o640)
   run(s,'chown screener:screener '+tmp+' && mv '+tmp+' '+path,quiet=True);changed=True
   code="import json;from tools.trade.screener_bot import PROFILES;from tools.trade.fast_monitor import run;p=json.loads(PROFILES.read_text());assert all(a[a.index('--max-open')+1]=='200' for a in p);raise SystemExit(run(['--once']))"
   run(s,'cd /opt/crypto-trade && sudo -u screener .venv/bin/python -c '+shlex.quote(code),quiet=True)
  except Exception:
   if changed and old is not None:put(s,path,old,0o640);run(s,'chown screener:screener '+path,quiet=True)
   raise
  finally:run(s,'systemctl start screener-bots.timer',quiet=True)
  run(s,'systemctl start screener-bots.service screener-diagnostics.service',quiet=True)
  run(s,'systemctl start screener-health.service screener-backup.service',quiet=True)
  run(s,'systemctl show screener-bots.service screener-health.service screener-backup.service -p Id -p Result')
  code="import json,pathlib;r=pathlib.Path('/opt/crypto-trade/data/trade');p=json.loads((r/'screener-profiles.json').read_text());print([(a[a.index('--name')+1],a[a.index('--max-open')+1]) for a in p]);pages=list(r.glob('screener-*/bot.html'));print('pages verified',len(pages));assert len(pages)==7;assert all('из 200' in x.read_text() for x in pages)"
  run(s,'python3 -c '+shlex.quote(code))
  print('Capacity 200 installed; backup before-limit-'+stamp)
 finally:s.close()

if __name__=='__main__':main()
