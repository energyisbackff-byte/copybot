import os,json,time,urllib.request,urllib.parse,bisect,logging
log=logging.getLogger("bot")
WSOL="So11111111111111111111111111111111111111112";USDC="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";USDT="Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe5BenwNYB"
QUOTES={WSOL,USDC,USDT}
def _post(u,body,timeout=90):
    for a in range(5):
        try:return json.load(urllib.request.urlopen(urllib.request.Request(u,json.dumps(body).encode(),{"content-type":"application/json"}),timeout=timeout))
        except Exception as e:
            log.debug("retry %s",e);time.sleep(2*(a+1))
    raise RuntimeError("request failed")
def _get(u,timeout=20,headers=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers=headers or {"user-agent":"copybot-paper"}),timeout=timeout))
class Helius:
    def __init__(s): s.u="https://mainnet.helius-rpc.com/?api-key="+os.environ["HELIUS_API_KEY"].strip();s.calls=0
    def rpc(s,m,p):
        s.calls+=1;r=_post(s.u,{"jsonrpc":"2.0","id":1,"method":m,"params":p})
        if "error" in r: raise RuntimeError(f"{m}: {r['error'].get('message')}")
        return r["result"]
    def history(s,wallet,since_ts,max_pages=15):
        out=[];tok=None
        for _ in range(max_pages):
            o={"transactionDetails":"full","limit":100,"maxSupportedTransactionVersion":1,"encoding":"jsonParsed","sortOrder":"desc"}
            if tok:o["paginationToken"]=tok
            r=s.rpc("getTransactionsForAddress",[wallet,o]);stop=False
            for t in r["data"]:
                if (t.get("blockTime") or 0)<since_ts: stop=True;break
                e=parse_tx(t,wallet)
                if e: out.append(e)
            tok=r.get("paginationToken")
            if stop or not tok: break
        return out,(not tok) or stop
    def new_sigs(s,wallet,until=None):
        p={"limit":25}
        if until:p["until"]=until
        return s.rpc("getSignaturesForAddress",[wallet,p])
    def tx(s,sig):
        return s.rpc("getTransaction",[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":1}])
def parse_tx(t,w):
    m=t.get("meta") or {}
    if not m or m.get("err"):return None
    ak=t["transaction"]["message"]["accountKeys"];keys=[k["pubkey"] if isinstance(k,dict) else k for k in ak]
    if w not in keys:return None
    i=keys.index(w);signer=isinstance(ak[i],dict) and ak[i].get("signer")
    sol=(m["postBalances"][i]-m["preBalances"][i]+(m["fee"] if signer else 0))/1e9
    d={};pre={}
    for side,sg in(("pre",-1),("post",1)):
        for b in m.get(side+"TokenBalances") or []:
            if b.get("owner")!=w:continue
            a=float(b["uiTokenAmount"]["uiAmountString"] or 0);d[b["mint"]]=d.get(b["mint"],0)+sg*a
            if side=="pre":pre[b["mint"]]=pre.get(b["mint"],0)+a
    if WSOL in d: sol+=d.pop(WSOL)
    d={k:v for k,v in d.items() if abs(v)>1e-12}
    if not d and abs(sol)<1e-6:return None
    return {"t":t.get("blockTime"),"sig":t["transaction"]["signatures"][0],"sol":sol,"tok":d,"pre":pre}
_px={"t":0,"sol":None}
def sol_usd():
    if time.time()-_px["t"]>300 or not _px["sol"]:
        try:_px["sol"]=float(_get(f"https://lite-api.jup.ag/price/v3?ids={WSOL}")[WSOL]["usdPrice"]);_px["t"]=time.time()
        except Exception as e: log.warning("SOL price failed %s",e)
    return _px["sol"] or 100.0
def classify(e,solp):
    """-> (side, mint, token_delta, usd, pre_amount) or None"""
    tok=dict(e["tok"]);q=e["sol"]*solp
    for s in(USDC,USDT):
        if s in tok:q+=tok.pop(s)
    if len(tok)!=1:return None
    m,a=next(iter(tok.items()))
    if a>0 and q<-0.5:return("buy",m,a,-q,e["pre"].get(m,0))
    if a<0 and q>0.05:return("sell",m,a,q,e["pre"].get(m,0))
    return("xfer",m,a,0,e["pre"].get(m,0))
def roundtrips(events,solp,min_cost=5):
    pos={};trips=[]
    for e in sorted(events,key=lambda e:e["t"]):
        c=classify(e,solp)
        if not c:continue
        side,m,a,usd,_=c;p=pos.get(m)
        if side=="buy":
            if not p:p=pos[m]={"open":e["t"],"amt":0,"cost":0,"proc":0}
            p["amt"]+=a;p["cost"]+=usd
        elif side=="sell" and p:
            sell=min(-a,p["amt"]);p["proc"]+=usd*(sell/-a);p["amt"]-=sell
            if p["amt"]<=0.01*(p["amt"]+sell):
                if p["cost"]>=min_cost: trips.append({"mint":m,"open":p["open"],"close":e["t"],"cost":p["cost"],"pnl":p["proc"]-p["cost"]})
                del pos[m]
        elif side=="xfer" and p and a<0:
            r=min(1,-a/p["amt"]) if p["amt"] else 1;p["amt"]*=1-r;p["cost"]*=1-r
            if p["amt"]<=1e-9:del pos[m]
    return trips
def jup_price(mint):
    try:
        r=_get(f"https://lite-api.jup.ag/price/v3?ids={mint}").get(mint)
        return (float(r["usdPrice"]),float(r.get("liquidity") or 0)) if r else (None,0)
    except Exception as e: log.warning("jup price %s",e);return (None,0)
def jup_quote(inm,outm,amount_raw):
    try:
        r=_get("https://lite-api.jup.ag/swap/v1/quote?"+urllib.parse.urlencode({"inputMint":inm,"outputMint":outm,"amount":int(amount_raw),"slippageBps":300}))
        return int(r["outAmount"]),float(r.get("priceImpactPct") or 0)
    except Exception as e: log.info("jup quote failed %s",e);return None,None
_dec={}
def decimals(h,mint):
    if mint not in _dec:
        try:_dec[mint]=h.rpc("getTokenSupply",[mint])["value"]["decimals"]
        except Exception:_dec[mint]=6
    return _dec[mint]
class Fomo:
    def __init__(s): s.k=os.environ.get("FOMOAPI_KEY","").strip()
    def leaderboard(s,w,limit=50):
        return _get(f"https://api.fomoapi.io/v2/leaderboard/{w}?limit={limit}",30,{"authorization":"Bearer "+s.k})["traders"]
