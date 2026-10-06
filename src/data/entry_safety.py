"""Paper entry guardrails, not fitted profitability thresholds."""
import math

# Do not tighten a structural stop into market noise to make a trade fit.
# These conservative paper limits reject the setup; a new retest can qualify.
MAX_STOP_PCT={'5m':3.0,'15m':5.0,'1h':8.0,'4h':12.0}
MAX_STOP_ATR=3.0

def stop_guard(entry,stop,tf,atr=None):
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in (entry,stop)):
        return 'нет корректного входа и стопа'
    distance=abs(entry-stop);pct=distance/entry*100;limit=MAX_STOP_PCT.get(tf)
    if limit and pct>limit:
        return f'стоп слишком широкий для {tf}: {pct:.2f}% > {limit:g}%; ждём отдельный ретест'
    if isinstance(atr,(int,float)) and math.isfinite(atr) and atr>0 and distance>MAX_STOP_ATR*atr:
        return f'стоп {distance/atr:.2f} ATR до импульса > {MAX_STOP_ATR:g}; вход после импульса пропущен'
    return None

def prior_atr(bars,ts,n=14):
    history=[b for b in bars if b[0]<ts][-n-1:]
    if len(history)<n+1:return None
    return sum(max(b[2]-b[3],abs(b[2]-a[4]),abs(b[3]-a[4])) for a,b in zip(history,history[1:]))/n
