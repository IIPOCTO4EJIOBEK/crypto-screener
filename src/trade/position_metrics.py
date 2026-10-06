"""Position return on initial notional and current share of profile equity."""
from dataclasses import asdict,is_dataclass

def metrics(position,equity,fee=.0005):
 p=asdict(position) if is_dataclass(position) else position
 price=p.get('mark') or p['entry'];qty=p['qty'];sign=1 if p['side']=='long' else -1
 net=p.get('realized',0)+sign*(price-p['entry'])*qty-p.get('fee_in',0)-price*qty*fee
 initial=(p.get('qty0') or qty)*p['entry']
 return dict(pnl_usdt=net,pnl_pct=100*net/initial if initial else 0,
             exposure_pct=100*price*qty/equity if equity>0 else None,
             price_change_pct=100*sign*(price/p['entry']-1))

