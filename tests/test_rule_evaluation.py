from src.trade.rule_evaluation import metrics,net,new_rules,START
def test_partial_is_already_in_close_pnl_and_pending_is_separate():
 r={'pnl':12,'fee':2,'funding':1,'partial':4,'reason':'target','exit_ts':1}
 assert net(r)==9
 m=metrics([r,dict(r,funding_pending=True)])
 assert m['net']==9 and m['net_provisional']==18 and m['settled']==1 and m['pending']==1
def test_legacy_close_after_install_is_not_new_entry():
 assert not new_rules({'opened_ms':START-1,'ts':START+1,'rules':{'prior_atr':2}})
 assert not new_rules({'opened_ms':START+1,'rules':{}})
 assert new_rules({'opened_ms':START+1,'rules':{'prior_atr':None}})
def test_closed_curve_drawdown_and_break_even():
 rows=[{'pnl':v,'fee':0,'reason':'stop','exit_ts':i} for i,v in enumerate([2,-1,-2,4])]
 m=metrics(rows)
 assert m['closed_curve_drawdown_usdt']==3 and m['win_rate']==50
 assert m['break_even_win_rate']==100*1.5/(3+1.5)
