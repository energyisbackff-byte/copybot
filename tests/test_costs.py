"""Honest paper costs (copycost.py + paper.py cost_model), offline. Run: python3 tests/test_costs.py
Checks: the old fixed model is unchanged; the measured model = margin + network fee (+ coin account rent on a buy) and the
measured delay; defaults before the first probe; a probe records every part; the replay of past fills; /costs fits the
phone; only copy + consensus use it; with real money on, the paper adds no pretend delay."""
import sys,os,time,json,tempfile,re,html
R=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(R,"signer"));sys.path.insert(0,R);os.chdir(R)
T=tempfile.mkdtemp();os.environ.update(HELIUS_API_KEY="x",COPYBOT_MEMORY=os.path.join(T,"memory"),TELEGRAM_BOT_TOKEN="",COPYBOT_DB=os.path.join(T,"c.db"))
import yaml,copycost,paper,spot_live as SL,live as LV
from db import DB
from tgfmt import W
cfg=yaml.safe_load(open("config.yaml"));db=DB(os.environ["COPYBOT_DB"]);res={};PX=150.0
def txt(t):return html.unescape(re.sub(r"<[^>]+>","",t))
def fits(t):return all(len(l)<=W for pre in re.findall(r"<pre>(.*?)</pre>",t,re.S) for l in html.unescape(pre).split("\n"))
class N:
    token=""
    def send(s,*a,**k):pass
class H:
    def rpc(s,m,p):return {"value":{"blockhash":"x","lastValidBlockHeight":1}}
paper.sol_usd=lambda:PX
# ---- which books use which model --------------------------------------------------------------------------------------
P={b:paper.Paper(db,H(),cfg,N(),b) for b in("copy","consensus","narrative","trend","trend-basket")}
res["copy + consensus use measured costs; narrative, Trend Rider, basket keep their tested numbers"]=P["copy"].measured() and P["consensus"].measured() and not any(P[b].measured() for b in("narrative","trend","trend-basket"))
# ---- the old model is exactly the old numbers -------------------------------------------------------------------------
C0=json.loads(json.dumps(cfg));C0["paper"]["cost_model"]="fixed";old=paper.Paper(db,H(),C0,N(),"copy")
res["fixed model unchanged: 2% + $0.01 per side, 1.5 s"]=abs(old.fee(10,"buy")-0.21)<1e-12 and abs(old.fee(10,"sell")-0.21)<1e-12 and old.latency()==1.5 and abs(old.cost_side(10)-(0.02+0.001))<1e-12
# ---- measured model before any probe: defaults ---------------------------------------------------------------------------
M=copycost.measured(db,cfg)
res["before the first probe: default delay 6 s and the priority-fee cap (stated as not measured)"]=M["latency_s"]==6.0 and M["priority_lamports"]==100000 and not M["measured"]
cp=P["copy"]
buy=cp.fee(5,"buy");sell=cp.fee(5,"sell");net=(5000+100000)/1e9*PX;rent=0.00203928*PX
res["measured fee: 0.25% + network fee per side, + the coin account rent on the buy"]=abs(sell-(5*0.0025+net))<1e-9 and abs(buy-(5*0.0025+net+rent))<1e-9
res["cost share of a $5 round trip includes the rent (why tiny copies rarely pay)"]=abs(cp.cost_side(5)-(0.0025+(2*net+rent)/10))<1e-9 and cp.cost_side(5)>0.03
C1=json.loads(json.dumps(cfg));C1["paper"]["reclaim_rent"]=True;db.kv_set(copycost.KEY,None)
res["if the wallet got the rent back: no rent in the cost"]=abs(copycost.fee_usd(copycost.measured(db,C1),5,"buy",PX)-(5*0.0025+net))<1e-9
# ---- a probe measures every part --------------------------------------------------------------------------------------
LV.jup_quote_full=lambda i,o,a,b:(time.sleep(0.01),{"outAmount":"1"})[1]
copycost._swap_build=lambda q,u,m:(time.sleep(0.02),("Q",42000))[1]
SL.signer=lambda op,timeout=40,**k:{"wallet":"CoinWallet"} if op=="pubkey" else (time.sleep(0.005),{"ok":True})[1]
s=copycost.probe(db,cfg,H())
res["probe: Jupiter quote, swap build, priority fee, guard check, network round trip"]=all(k in s for k in("quote_s","build_s","guard_s","rpc_s")) and s["priority_lamports"]==42000
for i in range(4):db.kv_set(copycost.KEY,(db.kv_get(copycost.KEY) or [])+[dict(ts=time.time(),quote_s=0.4,build_s=0.5,guard_s=0.3,rpc_s=0.15,priority_lamports=50000)])
M=copycost.measured(db,cfg);pt=M["parts"]
res["measured delay = quote + build + guard + 2 network + pickup + landing + margin"]=abs(M["latency_s"]-(0.4+0.5+0.3+0.3+1.5+1.2+1.0))<0.02 and M["measured"] and M["priority_lamports"]==50000
res["probe is due again after an hour"]=not copycost.due(db,cfg) and copycost.due(db,cfg,now=time.time()+3700)
cp._mc=None
res["paper copy waits the measured delay (not 1.5 s)"]=abs(cp.latency()-M["latency_s"])<1e-9
m=SL.arm_map(db);db.kv_set("boot_id","b1");m["copy"]=dict(boot="b1",until=time.time()+3600,mode="trial");db.kv_set("spot-live:armed",m)
res["real money on for copy: the paper adds no pretend delay (the real trade is the delay)"]=cp.latency()==0.0 and P["consensus"].latency()>0
db.kv_set("spot-live:armed",None)
# ---- replay of the past fills ----------------------------------------------------------------------------------------
def fill(book,side,size,fee,our,q):db.ins("b_fills",ts=time.time(),book=book,sig="s",trader="t",mint="m",side=side,status="filled",reason="",size_usd=size,fee_usd=fee,our_px=our,quote_px=q)
fill("copy","buy",5.0,0.11,1.02,1.00);fill("copy","sell",4.0,0.09,0.99,1.00)
db.ins("b_trades",ts=time.time(),book="copy",trader="t",mint="m",side="sell",usd=3.9,tokens=1,price=1,pnl=-1.2,sig="s",note="")
r=copycost.replay(db,cfg,PX)["copy"];M=copycost.measured(db,cfg)
nf=5*0.0025+M["net_sol"]*PX+M["rent_sol"]*PX+4*0.0025+M["net_sol"]*PX
rate=((0.02)+(0.01))/2/1.5;extra=rate*(M["latency_s"]-1.5)*9.0
res["replay: new P&L = old P&L + old fees - new fees - extra delay cost"]=abs(r["new_fees"]-nf)<1e-9 and abs(r["delay_cost"]-extra)<1e-9 and abs(r["new_pnl"]-(-1.2+0.20-nf-extra))<1e-9
res["replay: also shown with the rent back"]=abs(r["new_pnl_rent_back"]-r["new_pnl"]-M["rent_sol"]*PX)<1e-9
fill("consensus","buy",5.0,0.11,0.99,1.00);fill("consensus","sell",5.0,0.11,1.01,1.00)
res["replay: a favourable price move is never counted as a gain (no luck assumed)"]=copycost.replay(db,cfg,PX)["consensus"]["delay_cost"]==0
t=copycost.text(db,cfg,PX)
res["/costs: old vs new, the measured parts, the past re-done, fits the phone"]=all(x in txt(t) for x in("old","new","delay","copy:","if rent back")) and fits(t)
import cmds;res["/costs command answers"]="honest paper costs" in txt(cmds.handle("/costs",db,"/costs",cfg))
res["the worker measures about hourly and announces the new costs once"]="copycost.probe(db,cfg,hel)" in open("spot_live.py").read() and 'kv_get("costs:announced")' in open("spot_live.py").read()
import tgfmt;res["Telegram menu has /costs"]=any(c=="costs" for c,_ in tgfmt.COMMANDS)
for k,v in res.items():print("PASS" if v else "FAIL",k)
bad=[k for k,v in res.items() if not v];print(f"{len(res)-len(bad)}/{len(res)} passed");sys.exit(1 if bad else 0)
