"""CT narrative engine. Rule zero of the new books: NO NARRATIVE, NO TRADE.
Every cycle (narrative.cycle_minutes) it:
  1 reads the sources (feeds.py): X/Twitter posts (official API, your key and budget), crypto news RSS, Dexscreener
    'metas' (the market's own trending themes), GeckoTerminal trending pools, Dexscreener boosts (paid promotion: a
    warning sign, never evidence)
  2 turns them into weighted mentions per ticker. A post's weight depends on the account's age, followers and its past
    call accuracy (did coins it named rise 10%+ within 24h?); bursts of new/low-follower accounts or copy-pasted text
    are down-weighted (sybil filter). Raw counts mean nothing.
  3 measures each ticker's mention VELOCITY (last hour vs its own trailing 24h baseline), ACCELERATION (last hour vs
    the hour before), unique quality accounts and engagement speed
  4 groups tickers into named narratives (Dexscreener metas + coins co-mentioned on X) and tracks each one's stage:
    emerging -> accelerating -> peaking -> decaying. Only emerging/accelerating may trade; decaying (heat -50% from its
    peak, or falling N checks in a row) blocks new entries
  5 maps each narrative to the coin really riding it: strongest 1h volume growth with price confirmation, enough
    liquidity, an old-enough pair (the rug checks run again at the buy)
  6 scores every candidate 0-100, writes the evidence (the 7 questions) and logs every score, traded or not
Only an A+ candidate (score >= a_plus AND every gate) is handed to the Narrative Rider. If the evidence is missing the
answer is no trade, whatever the chart looks like. Nothing here guesses with an AI model: every number is counted."""
import re,time,json,math,hashlib,logging,statistics as st
import feeds as FD,risk
log=logging.getLogger("bot")
DDL=["create table if not exists mentions(id integer primary key,ts real,seen real,source text,family text,ticker text,account text,weight real,engagement real,thash text,words text)",
     "create index if not exists mentions_t on mentions(ticker,ts)",
     "create table if not exists x_accounts(account text primary key,followers integer,age_days real,calls integer default 0,hits integer default 0,updated real)",
     "create table if not exists x_calls(id integer primary key,ts real,account text,ticker text,mint text,px real,judged integer default 0,hit integer)",
     "create table if not exists narratives(id text primary key,name text,state text,first_seen real,updated real,peak real,data text)",
     "create table if not exists narrative_scores(id integer primary key,ts real,narrative text,name text,state text,ticker text,mint text,score real,grade text,traded integer default 0,evidence text)",
     "create index if not exists narrative_scores_t on narrative_scores(ts)"]
DEF=dict(enabled=True,cycle_minutes=5,news_every_minutes=15,window_h=1.0,baseline_h=24.0,a_plus=85,min_liquidity_usd=50000,min_pair_age_h=1.0,
         min_families=2,require_social=True,decay_from_peak=0.5,decay_checks=3,raw_keep_days=14,metas_top=6,
         majors=["SOL","XRP","DOGE","LINK","UNI","ATOM","BTC","ETH"],
         x=dict(enabled=True,daily_read_budget=300,every_minutes=15,queries={
             "memes":"(solana OR memecoin OR pumpfun OR \"pump.fun\") has:cashtags -is:retweet -is:reply lang:en",
             "majors":"($XRP OR $DOGE OR $LINK OR $UNI OR $ATOM OR $SOL) -is:retweet -is:reply lang:en"}))
NAMES={"bitcoin":"BTC","ethereum":"ETH","ether":"ETH","solana":"SOL","ripple":"XRP","xrp":"XRP","dogecoin":"DOGE","chainlink":"LINK",
       "uniswap":"UNI","cosmos":"ATOM"}
BULL=("etf","approval","approved","approves","partnership","launch","launches","upgrade","adoption","surge","surges","rally","rallies","record",
      "inflow","inflows","bullish","breakout","listing","lists","integrat","wins","win","soar","soars","jump","jumps")
BEAR=("hack","hacked","exploit","lawsuit","sues","sued","charges","delist","ban","bans","outflow","outflows","plunge","plunges","crash","crashes",
      "bearish","liquidat","dump","dumps","fraud","probe","halt","drops","falls","slump")
STOP=set("the a an and or of to in on for with is are was be by at as it its this that from new now will has have just more than over after into crypto coin coins token tokens market price".split())
CASHTAG=re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")
def norm_text(t):
    t=re.sub(r"https?://\S+|@\w+|\d+"," ",(t or "").lower());return " ".join(t.split())
def thash(t):return hashlib.sha1(norm_text(t).encode()).hexdigest()[:12]
def words(t):return [w for w in re.findall(r"[a-z]{3,}",norm_text(t)) if w not in STOP][:12]
def sentiment(texts):
    b=r=0
    for t in texts:
        tl=" "+(t or "").lower()+" "
        b+=sum(1 for w in BULL if re.search(r"\b"+w,tl));r+=sum(1 for w in BEAR if re.search(r"\b"+w,tl))
    return (b-r)/(b+r+1)
def quality_weight(followers,age_days,calls=0,hits=0,verified=False):
    """How much one post counts. New accounts and tiny accounts count little; a good track record counts more."""
    aw=0.2 if age_days is not None and age_days<30 else 0.6 if age_days is not None and age_days<180 else 1.0
    fw=min(1.5,max(0.25,math.log10((followers or 0)+10)/4))
    accw=0.5+(hits+1)/(calls+2)
    return round(aw*fw*accw*(1.1 if verified else 1.0),4)
def extract(text,known=()):
    """Tickers named in a text: $cashtags, coin names, and known symbols written in capitals."""
    out={m.upper() for m in CASHTAG.findall(text or "")}
    low=" "+(text or "").lower()+" "
    for n,t in NAMES.items():
        if re.search(r"\b"+re.escape(n)+r"\b",low):out.add(t)
    for k in known:
        if len(k)>=3 and re.search(r"(?<![A-Za-z$])"+re.escape(k)+r"(?![A-Za-z])",text or ""):out.add(k)
    return out
def stage(hist,peak,c,first_seen,now):
    """Narrative life stage from its heat history [(ts, heat)], newest last."""
    if not hist:return "quiet"
    h=[x[1] for x in hist];cur=h[-1]
    falls=0
    for a,b in zip(h[::-1][1:],h[::-1][:-1]):
        if b<a:falls+=1
        else:break
    if peak>0 and cur<=peak*(1-c["decay_from_peak"]) or falls>=c["decay_checks"]:return "decaying"
    acc=len(h)>=3 and h[-1]>h[-2]>h[-3]
    if cur<1.5:return "quiet"
    if acc and cur>=3:return "accelerating"
    if now-first_seen<6*3600 and (len(h)<2 or h[-1]>=h[-2]):return "emerging"
    if len(h)>=2 and h[-1]<=h[-2] and cur>=0.8*peak:return "peaking"
    return "accelerating" if len(h)>=2 and h[-1]>h[-2] else "peaking"
def coin_points(p,c,now):
    """Market confirmation for one coin: (market points 0-25, liquidity points 0-10, gate failures, facts)."""
    fails=[];vg=p["vol"]["h1"]/max(p["vol"]["h6"]/6,1.0) if p["vol"]["h6"] else (2.0 if p["vol"]["h1"] else 0)
    ch1=p["ch"]["h1"];ch5=p["ch"]["m5"];liq=p["liq"];age_h=(now-p["created"])/3600 if p.get("created") else None
    pts=10*min(1,max(0,(vg-1)/3))+10*min(1,max(0,ch1)/0.2)+(5 if ch5>=0 else 0)-(5 if ch1>1.0 else 0)
    lq=10 if liq>=4*c["min_liquidity_usd"] else 7 if liq>=2*c["min_liquidity_usd"] else 4 if liq>=c["min_liquidity_usd"] else 0
    if liq<c["min_liquidity_usd"]:fails.append(f"liquidity ${liq:,.0f} < ${c['min_liquidity_usd']:,.0f}")
    if age_h is not None and age_h<c["min_pair_age_h"]:fails.append(f"pair only {age_h*60:.0f} min old")
    if ch1<=0:fails.append(f"price not confirming (1h {ch1:+.1%})")
    if ch5<-0.03:fails.append(f"dumping right now (5m {ch5:+.1%})")
    return max(0,min(25,pts)),lq,fails,dict(vol_growth=round(vg,2),ch_h1=ch1,ch_m5=ch5,liq=liq,age_h=round(age_h,1) if age_h is not None else None,mcap=p.get("mcap"))
class Engine:
    def __init__(s,db,cfg,notify=None,feeds=None):
        s.db=db;s.cfg=cfg;s.n=notify;s.c=dict(DEF,**((cfg or {}).get("narrative") or {}));s.c["x"]=dict(DEF["x"],**(s.c.get("x") or {}))
        s.F=feeds or FD.Feeds(db,cfg)
        for q in DDL:db.c.execute(q)
        db.c.commit();s.known=set(s.c["majors"]);s.symbol_mint={};s.last={}
    def due(s,k,minutes,now):
        t=s.db.kv_get("narr:due:"+k,0)
        if now-t>=minutes*60:s.db.kv_set("narr:due:"+k,now);return True
        return False
    # ---- 1-2 ingest ------------------------------------------------------------------------------------
    def add_mention(s,ts,source,family,ticker,account=None,weight=1.0,engagement=0,text="",now=None):
        s.db.c.execute("insert into mentions(ts,seen,source,family,ticker,account,weight,engagement,thash,words) values(?,?,?,?,?,?,?,?,?,?)",
            (ts,now or time.time(),source,family,ticker,account,weight,engagement,thash(text) if text else None," ".join(words(text)) if text else None))
    def ingest_x(s,now):
        if not s.F.x_enabled():return 0
        if not s.due("x",s.c["x"]["every_minutes"],now):return 0
        n=0;Q=s.c["x"]["queries"];per=max(10,s.F.x_budget_left()//max(1,len(Q)*int(max(1,(86400-now%86400)/(s.c["x"]["every_minutes"]*60)))))
        for name,q in Q.items():
            for p in s.F.x_search(name,q,want=min(100,per)):
                acc=s.db.q("select calls,hits from x_accounts where account=?",(p["author"],));calls,hits=acc[0] if acc else (0,0)
                s.db.c.execute("insert into x_accounts(account,followers,age_days,calls,hits,updated) values(?,?,?,?,?,?) on conflict(account) do update set followers=excluded.followers,age_days=excluded.age_days,updated=excluded.updated",
                    (p["author"],p["followers"],p["account_age_days"],calls,hits,now))
                w=quality_weight(p["followers"],p["account_age_days"],calls,hits,p["verified"])
                tk=set(p["cashtags"])|extract(p["text"],())
                if len(tk)>6:w/=2                         # a post naming many coins is spam-like
                for t in tk:s.add_mention(p["ts"],"x:"+name,"x",t,p["author"],w,p["engagement"],p["text"],now);n+=1
        s.db.c.commit();return n
    def ingest_news(s,now):
        if not s.due("news",s.c["news_every_minutes"],now):return 0
        seen=set(s.db.kv_get("narr:news_seen") or []);n=0;new=[]
        for it in s.F.news():
            if it["id"] in seen or now-it["ts"]>2*86400:continue
            new.append(it["id"])
            for t in extract(it["title"]+" "+it["summary"],s.known):
                s.add_mention(it["ts"],"news:"+it["source"],"news",t,it["source"],2.0,0,it["title"],now);n+=1
        s.db.kv_set("narr:news_seen",(list(seen)+new)[-800:]);s.db.c.commit();return n
    def ingest_market(s,now):
        """Dexscreener metas (named market themes) + GeckoTerminal trending. One mention per token per cycle."""
        metas=sorted(s.F.ds_metas(),key=lambda m:-(m["ch"].get("h1") or 0))[:s.c["metas_top"]];M={}
        for m in metas:
            pairs=s.F.ds_meta_pairs(m["slug"])
            M[m["slug"]]=dict(m,pairs=pairs)
            for p in pairs:
                s.known.add(p["symbol"]);s.symbol_mint[p["symbol"]]=p["mint"]
                s.add_mention(now,"dexscreener:"+m["slug"],"dexscreener",p["symbol"],None,1.0,0,"",now)
        trend=s.F.gt_trending()
        for p in trend:
            if p["mint"]:s.known.add(p["symbol"]);s.symbol_mint.setdefault(p["symbol"],p["mint"])
            s.add_mention(now,"geckoterminal","geckoterminal",p["symbol"],None,1.0,0,"",now)
        boosts=s.F.ds_boosts();s.db.c.commit()
        return M,trend,boosts
    # ---- 3 per-ticker social stats -----------------------------------------------------------------------
    def social(s,ticker,now):
        w=s.c["window_h"]*3600;b=s.c["baseline_h"]*3600
        R=s.db.q("select ts,family,account,weight,engagement,thash from mentions where ticker=? and family in('x','news') and ts>=?",(ticker,now-b-w))
        cur=[r for r in R if r[0]>=now-w];prev=[r for r in R if now-2*w<=r[0]<now-w];base=[r for r in R if r[0]<now-w]
        v=sum(r[3] for r in cur);vp=sum(r[3] for r in prev);vb=sum(r[3] for r in base)/max(1.0,(b-w)/3600)*(w/3600)
        ratio=v/max(vb,0.5);accel=(v-vp)/max(vp,1.0)
        accs={};hs={}
        for r in cur:
            if r[2]:accs[r[2]]=max(accs.get(r[2],0),r[3])
            if r[5]:hs[r[5]]=hs.get(r[5],0)+1
        acc_rows={a:s.db.q("select followers,age_days from x_accounts where account=?",(a,)) for a in accs if a}
        weak=sum(1 for a,x in acc_rows.items() if x and ((x[0][1] or 999)<30 or (x[0][0] or 0)<50))
        dup=sum(n for n in hs.values() if n>=3)
        sybil=min(1.0,(weak/len(accs) if accs else 0)*0.7+(dup/len(cur) if cur else 0))
        eng=sum(r[4] or 0 for r in cur);engb=sum(r[4] or 0 for r in base)/max(1.0,(b-w)/3600)*(w/3600)
        fam={r[1] for r in cur}
        return dict(v=round(v,3),prev=round(vp,3),base=round(vb,3),ratio=round(ratio,2),accel=round(accel,2),uniq=round(sum(accs.values()),2),n=len(cur),
            accounts=len(accs),sybil=round(sybil,2),eng_ratio=round(eng/max(engb,1.0),2),families=sorted(fam))
    def top_texts(s,tickers,now,n=3):
        w=s.c["window_h"]*3600;q=",".join("?"*len(tickers))
        return s.db.q(f"select source,account,words from mentions where ticker in ({q}) and family in('x','news') and ts>=? order by weight desc limit ?",tuple(tickers)+(now-w,n))
    # ---- 4 clusters --------------------------------------------------------------------------------------
    def clusters(s,M,now):
        """Named narratives: each Dexscreener meta, plus groups of coins co-mentioned on X in the last 6 hours."""
        N={}
        for slug,m in M.items():
            N["ds:"+slug]=dict(id="ds:"+slug,name=m["name"] or slug,tickers=sorted({p["symbol"] for p in m["pairs"]})[:40],pairs=m["pairs"],meta=m)
        rows=s.db.q("select thash,ticker from mentions where family='x' and ts>=? and thash is not null",(now-6*3600,))
        posts={}
        for h,t in rows:
            if t not in s.c["majors"]:posts.setdefault(h,set()).add(t)
        parent={}
        def f(x):
            while parent.setdefault(x,x)!=x:x=parent[x]
            return x
        pair={}
        for ts_ in posts.values():
            L=sorted(ts_)
            for i in range(len(L)):
                for j in range(i+1,len(L)):pair[(L[i],L[j])]=pair.get((L[i],L[j]),0)+1
        for (a,b),k in pair.items():
            if k>=2:parent[f(a)]=f(b)
        groups={}
        for t in {t for ts_ in posts.values() for t in ts_}:groups.setdefault(f(t),set()).add(t)
        for g in groups.values():
            hit=None
            for nid,n in N.items():
                if nid.startswith("ds:") and g&set(n["tickers"]):hit=nid;break
            if hit:N[hit]["social_tickers"]=sorted(g&set(N[hit]["tickers"]));continue
            lead=max(g,key=lambda t:s.db.q("select coalesce(sum(weight),0) from mentions where ticker=? and ts>=?",(t,now-3600))[0][0])
            N["x:"+lead]=dict(id="x:"+lead,name=("$"+lead+(" & co" if len(g)>1 else "")),tickers=sorted(g),pairs=[],meta=None)
        for t in s.c["majors"]:
            if s.db.q("select 1 from mentions where ticker=? and family in('x','news') and ts>=? limit 1",(t,now-3600)):
                N.setdefault("major:"+t,dict(id="major:"+t,name=t+" news/CT",tickers=[t],pairs=[],meta=None,major=True))
        return N
    # ---- 5-6 heat, stage, coin, score -----------------------------------------------------------------------
    def heat(s,n,soc):
        social=[x for x in soc.values() if x["n"]]
        if social:return max(x["ratio"] for x in social),"social"
        m=n.get("meta")
        if m:return round(1+max(0,(m["ch"].get("h1") or 0))*10,2),"market"
        return 0.0,"none"
    def track(s,n,h,now):
        r=s.db.q("select state,first_seen,peak,data from narratives where id=?",(n["id"],))
        st0,first,peak,data=(r[0][0],r[0][1],r[0][2] or 0,json.loads(r[0][3] or "{}")) if r else ("new",now,0,{})
        hist=[x for x in data.get("hist",[]) if now-x[0]<24*3600]+[[now,h]];peak=max([x[1] for x in hist]+[0])
        stg=stage(hist,peak,s.c,first,now)
        if stg in("emerging","accelerating") and st0=="decaying" or stg=="accelerating":risk.narrative_reaccelerated(s.db,n["id"])
        s.db.c.execute("insert into narratives(id,name,state,first_seen,updated,peak,data) values(?,?,?,?,?,?,?) on conflict(id) do update set name=excluded.name,state=excluded.state,updated=excluded.updated,peak=excluded.peak,data=excluded.data",
            (n["id"],n["name"],stg,first,now,peak,json.dumps(dict(hist=hist[-48:],tickers=n["tickers"][:40]))))
        return stg,first,peak,hist
    def candidates(s,n,boosts,now):
        """Coins of this narrative with fresh market data, best first."""
        mints={p["mint"] for p in n["pairs"] if p.get("mint")}
        for t in n.get("social_tickers") or n["tickers"]:
            if t in s.c["majors"]:continue
            m=s.symbol_mint.get(t)
            if not m:
                p=s.F.ds_search(t) if len(mints)<8 else None
                if p:m=p["mint"];s.symbol_mint[t]=m
            if m:mints.add(m)
        D=s.F.ds_tokens(list(mints)[:30]) if mints else {};out=[]
        for m,p in D.items():
            mk,lq,fails,facts=coin_points(p,s.c,now);out.append(dict(p,mkt=mk,lq=lq,pts=mk+lq,fails=fails,facts=facts,boosted=m in boosts))
        return sorted(out,key=lambda x:(not x["fails"],x["pts"]),reverse=True)
    def score(s,n,stg,first,soc,coin,now):
        sc=[x for x in soc.values() if x["n"]];best=max(sc,key=lambda x:x["v"]) if sc else None
        fam=set()
        for x in sc:fam|=set(x["families"])
        if n.get("meta"):fam.add("dexscreener")
        if coin and s.db.q("select 1 from mentions where family='geckoterminal' and ticker=? and ts>=? limit 1",(coin["symbol"],now-1800)):fam.add("geckoterminal")
        parts={}
        parts["social heat"]=25*min(1,math.log2(max(best["ratio"],1))/4) if best else 0
        parts["acceleration"]=10*min(1,max(0,best["accel"])/2) if best else 0
        uq=sum(x["uniq"] for x in sc);parts["quality accounts"]=15*min(1,uq/10)
        parts["independent sources"]=15 if len(fam)>=3 else 8 if len(fam)==2 else 0
        if coin:parts["coin confirmation"]=coin["mkt"];parts["liquidity"]=coin["lq"]
        sy=max([x["sybil"] for x in sc]+[0]);pen={}
        if sy>0:pen["fake-account burst"]=-20*sy
        if coin and coin.get("boosted") and not sc:pen["paid promotion without real talk"]=-10
        if now-first>48*3600:pen["old narrative (48h+)"]=-5
        if stg=="peaking":pen["peaking"]=-10
        total=max(0,min(100,sum(parts.values())+sum(pen.values())))
        if stg=="decaying":total=min(total,40)
        gates=[]
        if stg not in("emerging","accelerating"):gates.append(f"stage is {stg} (only emerging/accelerating trade)")
        if len(fam)<s.c["min_families"]:gates.append(f"only {len(fam)} independent source(s) (need {s.c['min_families']})")
        if s.c["require_social"] and not fam&{"x","news"}:gates.append("no CT/news talk: market lists alone are not a narrative")
        if sy>=0.5:gates.append(f"looks like fake accounts ({sy:.0%})")
        if not coin:gates.append("no tradable coin found for it")
        elif coin["fails"]:gates+=coin["fails"]
        grade="A+" if total>=s.c["a_plus"] and not gates else "A" if total>=75 else "B" if total>=60 else "C"
        return round(total,1),grade,parts,pen,gates,sorted(fam),best
    def evidence(s,n,stg,coin,alts,soc,fam,best,parts,pen,gates,now):
        tx=s.top_texts(n.get("social_tickers") or n["tickers"],now)
        cause=None
        if coin and best:cause=f"mentions {best['ratio']}x their normal level (accel {best['accel']:+.0%}) while 1h volume is {coin['facts']['vol_growth']}x and price {coin['ch']['h1']:+.1%}"
        return {"1 catalyst":f"{n['name']} ({stg})"+(f": "+"; ".join(f"{a or src}: {w}" for src,a,w in tx if w)[:300] if tx else ""),
            "2 sources":", ".join(fam) or "none","3 cause":cause or "no measured link between talk and price",
            "4 direction":(f"long only (spot): 1h {coin['ch']['h1']:+.1%}, 5m {coin['ch']['m5']:+.1%}" if coin else "–"),
            "5 edge after costs":"checked at the buy: first target must pay 3x the measured round-trip costs",
            "6 invalidation":"stop 1.5x ATR below entry; exit early if the narrative starts decaying before the first target",
            "7 why this coin":(f"{coin['symbol']}: best volume growth {coin['facts']['vol_growth']}x with liquidity ${coin['liq']:,.0f}"+
                (" vs "+", ".join(f"{a['symbol']} {a['facts']['vol_growth']}x" for a in alts[:3]) if alts else " (only candidate)")) if coin else "–",
            "score parts":{k:round(v,1) for k,v in dict(parts,**pen).items()},"blocked by":gates}
    def cycle(s,now=None):
        now=now or time.time();t0=time.time()
        nx=s.ingest_x(now);nn=s.ingest_news(now);M,trend,boosts=s.ingest_market(now)
        N=s.clusters(M,now);out=[]
        for nid,n in N.items():
            soc={t:s.social(t,now) for t in (n.get("social_tickers") or n["tickers"])[:25]}
            h,kind=s.heat(n,soc)
            if h<1.2 and not n.get("meta"):continue
            stg,first,peak,hist=s.track(n,h,now)
            C=[] if n.get("major") else s.candidates(n,boosts,now);coin=C[0] if C else None
            total,grade,parts,pen,gates,fam,best=s.score(n,stg,first,soc,coin,now)
            ev=s.evidence(n,stg,coin,C[1:],soc,fam,best,parts,pen,gates,now)
            sen=sentiment([w for _,_,w in s.top_texts(n["tickers"],now,20) if w])
            row=dict(narrative=nid,name=n["name"],state=stg,heat=h,heat_kind=kind,peak=peak,ticker=coin["symbol"] if coin else (n["tickers"][0] if n.get("major") else None),
                     mint=coin["mint"] if coin else None,score=total,grade=grade,families=fam,gates=gates,evidence=ev,sentiment=round(sen,2),
                     coin=dict(coin["facts"],price=coin["price"],mint=coin["mint"]) if coin else None,first_seen=first)
            s.db.c.execute("insert into narrative_scores(ts,narrative,name,state,ticker,mint,score,grade,evidence) values(?,?,?,?,?,?,?,?,?)",
                (now,nid,n["name"],stg,row["ticker"],row["mint"],total,grade,json.dumps(dict(ev,families=fam,sentiment=row["sentiment"]))))
            out.append(row)
            if coin and best:s.record_calls(coin,now)
        s.db.c.commit();out.sort(key=lambda r:-r["score"])
        s.judge_calls(now);s.F.prune(s.c["raw_keep_days"])
        try:s.db.x("delete from mentions where ts<?",(now-3*86400,));s.db.x("delete from narrative_scores where ts<?",(now-30*86400,))
        except Exception:pass
        summ=dict(ts=now,took=round(time.time()-t0,1),x=s.F.x_enabled(),x_left=s.F.x_budget_left() if s.F.x_enabled() else 0,new_mentions=dict(x=nx,news=nn),
                  metas=len(M),trending=len(trend),gaps=len(s.F.gaps(1)),top=[{k:v for k,v in r.items() if k!="evidence"} for r in out[:8]])
        s.db.kv_set("narr:last",summ);s.alert(out,now);s.last=summ
        return out
    # ---- account track record: did the coins an account named rise 10% within 24h? ------------------------
    def record_calls(s,coin,now):
        for (a,) in s.db.q("select distinct account from mentions where family='x' and ticker=? and ts>=? and account is not null",(coin["symbol"],now-s.c["cycle_minutes"]*60*2)):
            if not s.db.q("select 1 from x_calls where account=? and mint=? and ts>?",(a,coin["mint"],now-86400)):
                s.db.c.execute("insert into x_calls(ts,account,ticker,mint,px) values(?,?,?,?,?)",(now,a,coin["symbol"],coin["mint"],coin["price"]))
    def judge_calls(s,now):
        P=s.db.q("select id,account,mint,px from x_calls where judged=0 and ts<? limit 60",(now-86400,))
        if not P:return 0
        D=s.F.ds_tokens(list({p[2] for p in P}))
        for i,a,m,px in P:
            if m not in D or not px:continue
            hit=1 if D[m]["price"]>=px*1.10 else 0
            s.db.c.execute("update x_calls set judged=1,hit=? where id=?",(hit,i))
            s.db.c.execute("update x_accounts set calls=calls+1,hits=hits+? where account=?",(hit,a))
        s.db.c.commit();return len(P)
    # ---- phone: new narrative + selected coin --------------------------------------------------------------
    def alert(s,out,now):
        if not s.n:return
        from tgfmt import term,kv,SEP,wrap
        sent=s.db.kv_get("narr:alerted") or {}
        for r in out:
            if r["grade"] not in("A+","A") or r["state"] not in("emerging","accelerating"):continue
            k=r["narrative"]+":"+str(r["mint"])
            if now-sent.get(k,0)<6*3600:continue
            sent[k]=now
            rows=[kv("narrative",r["name"]),kv("stage",r["state"]),kv("score",f"{r['score']:.0f}/100 ({r['grade']})"),kv("coin",r["ticker"] or "–"),kv("sources",", ".join(r["families"]))]
            rows+=[SEP]+sum((wrap(f"{k_[2:]}: {v}") for k_,v in r["evidence"].items() if k_[0].isdigit()),[])[:14]
            if r["gates"]:rows+=[SEP,"not tradable yet:"]+sum((wrap(g,"- ") for g in r["gates"][:4]),[])
            s.n.send(term("narrative --detect",rows,("OK","🧭 A+ narrative: coin selected" if r["grade"]=="A+" else "🧭 narrative forming (A)")),tier="instant" if r["grade"]=="A+" else "digest",
                     digest=f"narrative {r['name']} {r['score']:.0f}/100 ({r['state']}) coin {r['ticker']}")
        s.db.kv_set("narr:alerted",{k:v for k,v in sent.items() if now-v<86400})
    # ---- for the perps book: is there a corroborated narrative for this major, and which way does it lean? ---
    def major(s,ticker,now=None):
        now=now or time.time();soc=s.social(ticker,now)
        tx=[w for _,_,w in s.top_texts([ticker],now,30) if w]
        fam=set(soc["families"]);hot=soc["ratio"]>=2 and soc["n"]>=3
        return dict(ticker=ticker,ratio=soc["ratio"],accel=soc["accel"],mentions=soc["n"],families=sorted(fam),sentiment=round(sentiment(tx),2),
                    ok=hot and len(fam)>=1 and soc["sybil"]<0.5,why=(f"{ticker}: talk {soc['ratio']}x normal in {', '.join(sorted(fam)) or 'no source'}" if soc["n"] else f"{ticker}: no CT/news talk in the last hour"))
def text(db,n=6):
    """/narratives: the engine's latest view, plain words, phone width."""
    from tgfmt import term,kv,SEP,wrap
    L=db.kv_get("narr:last")
    if not L:return term("narratives",["the engine has not run yet","(first cycle a few minutes","after start)"],("WARN","no data yet"))
    rows=[kv("updated",time.strftime("%H:%M UTC",time.gmtime(L["ts"]))),kv("X feed",(f"on, {L['x_left']} reads left today" if L["x"] else "off: /setkey X_BEARER_TOKEN")),
          kv("sources",f"{L['metas']} metas, {L['trending']} trending"),kv("gaps 1h",L["gaps"]),SEP]
    for r in L["top"][:n]:
        rows+=[f"{r['score']:>3.0f} {r['grade']:<2} {r['state'][:12]}"]+wrap(f"{r['name']} -> {r['ticker'] or 'no coin'}","    ","    ")
        if r["gates"]:rows+=wrap("blocked: "+r["gates"][0],"    ","    ")
    if not L["top"]:rows.append("no narrative is hot right now")
    rows+=[SEP]+wrap("only A+ (score 85+, every gate) can trade. no narrative = no trade.")
    return term("narratives --top",rows,("OK","🧭 narratives"))
