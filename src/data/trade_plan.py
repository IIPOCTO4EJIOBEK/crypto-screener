"""Structural target planning; do not manufacture a 3R trade through a confirmed level."""
import math

def plan(entry,stop,direction,levels,min_rr=3,fee=.0005,expected_move=None):
 if direction not in ('long','short') or not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (entry,stop)):
  return dict(ok=False,why='нет корректного входа и стопа')
 up=direction=='long';sign=1 if up else -1;risk=sign*(entry-stop)
 if risk<=0:return dict(ok=False,why='стоп стоит с неверной стороны')
 risk_net=risk+fee*(entry+stop)
 required=(entry*(1+fee)+min_rr*risk_net)/(1-fee) if up else (entry*(1-fee)-min_rr*risk_net)/(1+fee)
 if required<=0:return dict(ok=False,why='для 3R не хватает допустимого диапазона цены')
 obstacles=sorted((sign*(x['price']-entry),x['price'],x.get('touches',0)) for x in levels
                  if x.get('kind')==('resistance' if up else 'support') and x.get('touches',0)>=2 and sign*(x['price']-entry)>0)
 target=required;why='3R после комиссий, до подтверждённого препятствия'
 if obstacles:
  _,level,touches=obstacles[0];target=level-sign*risk*.1
  if sign*(target-required)<0:
   return dict(ok=False,why=f'подтверждённый уровень {level:.7g} ({touches} касаний) раньше 3R',first_obstacle=level)
  why=f'перед подтверждённым уровнем {level:.7g} ({touches} касаний)'
 if expected_move is not None and math.isfinite(expected_move) and expected_move>0:
  cap=entry+sign*expected_move
  if sign*(cap-required)<0:
   return dict(ok=False,why=f'обычный ход свечей ({expected_move:.7g}) не покрывает 3R после расходов')
  if sign*(cap-target)<0: target=cap;why+='; ограничено обычным ходом свечей'
 reward_net=sign*(target-entry)-fee*(entry+target)
 return dict(ok=True,target=target,rr=sign*(target-entry)/risk,rr_net=reward_net/risk_net,why=why)
