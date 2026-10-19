"""Honest paper costs for the copy and consensus books (paper.cost_model: measured). Paper stays paper.
What a real copy really costs, on top of the real Jupiter quote the paper fill already uses (the quote holds the pool
fees, spread and price impact, so they are never counted twice):
  - network fee per transaction: base fee + the priority fee Jupiter actually asks for (measured from real swap builds)
  - the coin's token account: the first buy of a coin creates it and locks ~0.00204 SOL of rent; the wallet does not
    close empty accounts today, so that rent is counted as spent (reclaim_rent: true if that changes)
  - a small safety margin per side for what a quote can't show (price moving while the transaction lands, MEV)
How long a real copy takes, measured on this server: Jupiter quote + Jupiter swap build + the coin guard's check + two
network round trips (simulation and send) + the worker pickup (half its loop) + landing (~1.2 s) + a margin.
The old fixed model (2% per side + $0.01, 1.5 s) stays available as cost_model: fixed.
probe() runs in the spot-live worker thread about once an hour (it calls Jupiter, the guard and Helius; never trades).
replay() recomputes what the past copy/consensus paper trades would have made under the new costs (/costs)."""
import time,json,statistics as st,logging
from tgfmt import term,kv,usd,SEP,wrap
log=logging.getLogger("bot")
RENT_SOL=0.00203928;BASE_LAMPORTS=5000;KEY="costs:copy"
BONK="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
DEF=dict(margin_pct=0.0025,latency_margin_s=1.0,land_s=1.2,default_latency_s=6.0,default_priority_lamports=100000,
         reclaim_rent=False,probe_minutes=60,probe_usd=5.0)
OLD=dict(fee_pct=0.02,gas_usd=0.01,latency_s=1.5)
def conf(cfg):
    p=(cfg or {}).get("paper") or {}
    return dict(DEF,**{k:p[k] for k in DEF if k in p})
# ---- measuring ------------------------------------------------------------------------------------------------------
def _swap_build(quote,user,max_lamports):
    """Jupiter swap build like live.jup_swap_tx, but keeps the priority fee Jupiter chose."""
    import urllib.request
    from chain import JUP,_jh
    body=dict(quoteResponse=quote,userPublicKey=user,wrapAndUnwrapSol=True,dynamicComputeUnitLimit=True,
              prioritizationFeeLamports={"priorityLevelWithMaxLamports":{"maxLamports":int(max_lamports),"priorityLevel":"high"}})
    r=json.load(urllib.request.urlopen(urllib.request.Request(JUP+"/swap/v1/swap",json.dumps(body).encode(),dict(_jh(),**{"content-type":"application/json"})),timeout=20))
    return r.get("swapTransaction"),r.get("prioritizationFeeLamports")
def probe(db,cfg,hel=None):
    """One measurement of the real copy path (no trade, no signature). Kept in kv costs:copy (last 72)."""
    import live as LV,spot_live as SL
    from chain import USDC
    c=conf(cfg);sc=SL.conf(cfg);s=dict(ts=time.time());pc=time.perf_counter
    try:
        t=pc();q=LV.jup_quote_full(USDC,BONK,int(c["probe_usd"]*1e6),sc["slippage_bps"]);s["quote_s"]=pc()-t
    except Exception as e:log.info("cost probe quote: %s",e);return None
    wallet=None;guard=None
    for g,call in(("coin guard",SL.signer),("trend guard",LV.signer)):
        try:wallet=call("pubkey",timeout=5)["wallet"];guard=(g,call);break
        except Exception:continue
    if wallet:
        try:
            t=pc();tx,prio=_swap_build(q,wallet,sc["priority_max_lamports"]);s["build_s"]=pc()-t
            if prio is not None:s["priority_lamports"]=int(prio)
            if tx and guard and guard[0]=="coin guard":
                t=pc()
                try:guard[1]("check_static",tx=tx,timeout=10)
                except Exception:pass
                s["guard_s"]=pc()-t
        except Exception as e:log.info("cost probe build: %s",e)
    if hel:
        try:t=pc();hel.rpc("getLatestBlockhash",[{"commitment":"confirmed"}]);s["rpc_s"]=pc()-t
        except Exception as e:log.info("cost probe rpc: %s",e)
    db.kv_set(KEY,((db.kv_get(KEY) or [])+[s])[-72:]);return s
def due(db,cfg,now=None):
    S=db.kv_get(KEY) or [];now=now or time.time()
    return not S or now-S[-1]["ts"]>=conf(cfg)["probe_minutes"]*60
def measured(db,cfg):
    """-> the honest cost model now: delay (s) with its parts, priority fee, margin, rent. Defaults until measured."""
    import spot_live as SL
    c=conf(cfg);S=db.kv_get(KEY) or []
    def med(k,d):
        v=[x[k] for x in S if x.get(k) is not None];return (st.median(v),True) if v else (d,False)
    q,mq=med("quote_s",0.4);b,mb=med("build_s",0.5);g,mg=med("guard_s",0.3);r,mr=med("rpc_s",0.15);pr,mp=med("priority_lamports",c["default_priority_lamports"])
    pickup=SL.conf(cfg)["check_seconds"]/2
    parts=dict(quote=q,build=b,guard=g,network=2*r,pickup=pickup,land=c["land_s"],margin=c["latency_margin_s"])
    lat=sum(parts.values()) if S else c["default_latency_s"]
    return dict(latency_s=round(lat,2),parts={k:round(v,2) for k,v in parts.items()},priority_lamports=int(pr),margin_pct=c["margin_pct"],
                net_sol=(BASE_LAMPORTS+int(pr))/1e9,rent_sol=0.0 if c["reclaim_rent"] else RENT_SOL,samples=len(S),measured=bool(S and mq),
                measured_parts=[k for k,m in(("quote",mq),("build",mb),("guard",mg),("network",mr),("priority fee",mp)) if m])
# ---- the cost of one fill -----------------------------------------------------------------------------------------
def fee_usd(M,amount_usd,side,sol_px):
    """Measured model: margin on the amount + network fee (+ the coin account's rent on a buy)."""
    f=amount_usd*M["margin_pct"]+M["net_sol"]*sol_px
    if side=="buy":f+=M["rent_sol"]*sol_px
    return f
# ---- what the past would have been ----------------------------------------------------------------------------------
def replay(db,cfg,sol_px,M=None):
    """Past copy/consensus paper fills recomputed with the measured costs and delay. Estimates, explained in /costs."""
    M=M or measured(db,cfg);out={}
    for book in("copy","consensus"):
        F=db.q("select side,size_usd,fee_usd,our_px,quote_px from b_fills where book=? and status='filled' and side in('buy','sell')",(book,))
        old_pnl=db.q("select coalesce(sum(pnl),0),count(*) from b_trades where book=? and side='sell'",(book,))[0]
        if not F:out[book]=dict(fills=0,sells=old_pnl[1],old_pnl=old_pnl[0]);continue
        old_fees=sum(f[2] or 0 for f in F);new_fees=sum(fee_usd(M,f[1] or 0,f[0],sol_px) for f in F)
        rent=sum(M["rent_sol"]*sol_px for f in F if f[0]=="buy")
        drift=[((f[3]/f[4]-1) if f[0]=="buy" else (1-f[3]/f[4])) for f in F if f[3] and f[4]]
        rate=max(0.0,st.mean(drift)/OLD["latency_s"]) if drift else 0.0          # adverse price move per second (never assume luck)
        extra=rate*max(0.0,M["latency_s"]-OLD["latency_s"])*sum(f[1] or 0 for f in F)
        new=old_pnl[0]+old_fees-new_fees-extra
        out[book]=dict(fills=len(F),buys=sum(1 for f in F if f[0]=="buy"),sells=old_pnl[1],old_pnl=old_pnl[0],old_fees=old_fees,new_fees=new_fees,
                       delay_cost=extra,drift_per_s=rate,new_pnl=new,rent=rent,new_pnl_rent_back=new+rent)
    return out
def text(db,cfg,sol_px):
    M=measured(db,cfg);R=replay(db,cfg,sol_px,M);P=M["parts"]
    rows=["copy + consensus books:",f"{'':<10}{'old':<9}new",
          f"{'fee/side':<10}{'2%+$0.01':<9}{M['margin_pct']:.2%}+${M['net_sol']*sol_px:.3f}",
          f"{'coin acct':<10}{'none':<9}${M['rent_sol']*sol_px:.2f} once",
          f"{'delay':<10}{'1.5 s':<9}{M['latency_s']:.1f} s",SEP,"new delay, measured parts:"]
    rows+=wrap(", ".join(f"{k} {v:.2f}s" for k,v in P.items()),"  ","  ")
    rows+=wrap(("measured: "+", ".join(M["measured_parts"])+f" ({M['samples']} samples)") if M["measured"] else "not measured yet: defaults until the first probe (within the hour)","  ","  ")
    for b,r in R.items():
        rows+=[SEP,f"{b}: {r.get('sells',0)} sells in the past"]
        if not r.get("fills"):rows.append("  no fills to recompute");continue
        rows+=[kv("  old P&L",usd(r["old_pnl"],True),11),kv("  new P&L",usd(r["new_pnl"],True),11),
               kv("  fees",f"{usd(r['old_fees'])} -> {usd(r['new_fees'])}",11),kv("  delay",usd(-r["delay_cost"],True)+" (est.)",11),
               kv("  if rent back",usd(r["new_pnl_rent_back"],True),11)]
    rows+=[SEP]+wrap("rent = the ~0.002 SOL a new coin account locks. 'if rent back' = if the wallet closed empty coin accounts (a later guard change). The delay cost is an estimate from the price moves the paper fills saw.")
    rows+=wrap("paper stays paper; only the copy and consensus books use the new costs.")
    return term("costs --paper",rows,("OK","🧮 honest paper costs"))
