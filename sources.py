"""Candidate-trader sources. Each adapter yields dicts {handle, wallet, source, sol_share}.
Only official/legit APIs are used. Every candidate is then judged by the same on-chain Helius filters."""
import os,json,time,logging,urllib.request,urllib.parse
from chain import _get
log=logging.getLogger("bot");SOLNET=1399811149
def _short(w): return w[:4]+"…"+w[-4:]
# ---- FOMO leaderboards via fomoapi.io (free key) ----------------------------------------
def fomo(db,hel,fomo_api,cfg):
    sc=cfg["selection"];out=[]
    for w in sc.get("leaderboards",["7d","30d"]):
        try:
            rows=fomo_api.leaderboard(w,sc.get("leaderboard_limit",50) if w!="all" else 100);db.kv_set(f"lb_{w}",{"t":time.time(),"rows":rows})
        except Exception as e:
            log.warning("leaderboard %s failed (%s), using cache",w,e);rows=(db.kv_get(f"lb_{w}") or {}).get("rows",[])
        for r in rows:
            s=(r.get("wallets") or {}).get("solana")
            if not s:continue
            tt=r.get("topTokens") or [];share=sum(1 for x in tt if x.get("networkId")==SOLNET)/len(tt) if tt else 0
            out.append(dict(handle=r["handle"],wallet=s,source=f"fomo:{w}",sol_share=share))
    return out
# ---- Dexscreener trending (no key) -> recent buyers read on-chain via Helius --------------
def dexscreener_buyers(db,hel,fomo_api,cfg):
    c=cfg["sources"]["dexscreener_buyers"];toks=[]
    for ep in ("token-boosts/top/v1","token-boosts/latest/v1"):
        try:
            for x in _get("https://api.dexscreener.com/"+ep,20):
                if x.get("chainId")=="solana" and x["tokenAddress"] not in toks: toks.append(x["tokenAddress"])
        except Exception as e: log.warning("dexscreener %s: %s",ep,e)
    toks=toks[:c.get("max_tokens",20)];agg={}
    for m in toks:
        try:
            r=hel.rpc("getTransactionsForAddress",[m,{"transactionDetails":"full","limit":100,"maxSupportedTransactionVersion":1,"encoding":"jsonParsed","sortOrder":"desc"}])
        except Exception as e: log.warning("helius mint %s: %s",m[:6],e);continue
        for t in r["data"]:
            me=t.get("meta") or {}
            if me.get("err"):continue
            ak=t["transaction"]["message"]["accountKeys"];signer=ak[0]["pubkey"] if isinstance(ak[0],dict) else ak[0];d=0
            for side,sg in(("pre",-1),("post",1)):
                for b in me.get(side+"TokenBalances") or []:
                    if b.get("owner")==signer and b["mint"]==m: d+=sg*float(b["uiTokenAmount"]["uiAmountString"] or 0)
            if d>0: a=agg.setdefault(signer,{"tokens":set(),"n":0});a["tokens"].add(m);a["n"]+=1
    ranked=sorted(agg.items(),key=lambda kv:(-len(kv[1]["tokens"]),-kv[1]["n"]))[:c.get("max_wallets",60)]
    log.info("dexscreener_buyers: %d trending tokens -> %d buyer wallets, keeping %d",len(toks),len(agg),len(ranked))
    return [dict(handle="dex:"+_short(w),wallet=w,source=f"dexscreener-buyers({len(a['tokens'])} tok)",sol_share=1.0) for w,a in ranked]
# ---- Birdeye (free Standard key: BIRDEYE_API_KEY) -----------------------------------------
def birdeye(db,hel,fomo_api,cfg):
    k=os.environ.get("BIRDEYE_API_KEY","").strip()
    if not k: log.info("birdeye: no BIRDEYE_API_KEY, skipped");return []
    c=cfg["sources"]["birdeye"];h={"X-API-KEY":k,"x-chain":"solana","accept":"application/json","user-agent":"copybot-paper"};out=[]
    for typ in c.get("windows",["1W","30d"]):
        try:
            q=urllib.parse.urlencode({"type":typ,"sort_by":"realized_pnl","sort_type":"desc","offset":0,"limit":min(100,c.get("limit",50)),"min_realized_pnl":1})
            d=_get("https://public-api.birdeye.so/trader/gainers-losers?"+q,20,h).get("data") or {}
            for it in d.get("items",[]):
                w=it.get("address")
                if w: out.append(dict(handle="be:"+_short(w),wallet=w,source=f"birdeye:{typ}",sol_share=1.0))
        except Exception as e: log.warning("birdeye %s: %s",typ,e)
        time.sleep(1)   # wallet endpoints: 75 req/min
    return out
# ---- Nansen (free key: 100 trial credits then 10/day; NANSEN_API_KEY) ---------------------
def nansen(db,hel,fomo_api,cfg):
    k=os.environ.get("NANSEN_API_KEY","").strip()
    if not k: log.info("nansen: no NANSEN_API_KEY, skipped");return []
    try:
        req=urllib.request.Request("https://api.nansen.ai/api/v1/smart-money/dex-trades",json.dumps({"chains":["solana"]}).encode(),{"apiKey":k,"Content-Type":"application/json"})
        d=json.load(urllib.request.urlopen(req,timeout=30))
        rows=d.get("data") or d.get("result") or []
        ws={r.get("trader_address") or r.get("address") for r in rows if isinstance(r,dict)}
        return [dict(handle="nansen:"+_short(w),wallet=w,source="nansen:smart-money",sol_share=1.0) for w in ws if w]
    except Exception as e: log.warning("nansen: %s",e);return []
ADAPTERS={"fomo":fomo,"dexscreener_buyers":dexscreener_buyers,"birdeye":birdeye,"nansen":nansen}
def collect(db,hel,fomo_api,cfg):
    src=cfg.get("sources") or {"fomo":{"enabled":True}};seen={};stats={}
    for name,fn in ADAPTERS.items():
        if not (src.get(name) or {}).get("enabled",name=="fomo"):stats[name]="off";continue
        try: got=fn(db,hel,fomo_api,cfg)
        except Exception as e: log.warning("source %s failed: %s",name,e);got=[]
        new=0
        for c in got:
            w=c["wallet"]
            if w in seen: seen[w]["source"]+=","+c["source"];continue   # dedupe, keep first (FOMO handle wins)
            seen[w]=c;new+=1
        stats[name]=f"{len(got)} found, {new} new"
    log.info("sources: %s",stats);db.kv_set("source_stats",{"t":time.time(),"stats":stats})
    return list(seen.values()),stats
