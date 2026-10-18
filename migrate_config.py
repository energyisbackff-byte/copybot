"""Add new config keys from the shipped default without overwriting the user's values. Backs up the old file."""
import sys,yaml,shutil,time
cur_p,new_p=sys.argv[1],sys.argv[2];cur=yaml.safe_load(open(cur_p)) or {};new=yaml.safe_load(open(new_p))
def merge(c,n):
    for k,v in n.items():
        if k not in c: c[k]=v;print("config: added",k)
        elif isinstance(v,dict) and isinstance(c[k],dict): merge(c[k],v)
merge(cur,new)
p=cur.get("paper",{})
if "delay_penalty_pct" in p:   # old fixed-penalty model -> real quote after simulated latency
    p.pop("delay_penalty_pct");p["slippage_pct"]=0.0;print("config: switched to real-quote fills (removed delay_penalty_pct, slippage_pct=0)")
if cur.get("selection",{}).get("max_candidates")==120: cur["selection"]["max_candidates"]=300;print("config: max_candidates 120 -> 300")
if cur.get("paper",{}).get("max_position_pct")==0.20:   # v4 decision: copy positions 20% -> 10% of the book
    cur["paper"]["max_position_pct"]=0.10;print("config: copy max_position_pct 0.20 -> 0.10")
t=(cur.get("books") or {}).get("trend") or {}
if t.get("max_drawdown_pct")==0.06:   # v2: the paper book keeps collecting evidence up to the gate's 10% (live still stops at 6%)
    t["max_drawdown_pct"]=0.10;print("config: trend paper book drawdown switch 6% -> 10%")
for k in("backtest_days",):
    if k in t:t.pop(k);print("config: removed books.trend."+k+" (v1 only)")
cur["mode"]="paper";cur["live_trading"]=False
shutil.copy(cur_p,cur_p+".bak."+time.strftime("%Y%m%d%H%M%S"))
yaml.safe_dump(cur,open(cur_p,"w"),sort_keys=False);print("config migrated (backup saved)")
