"""Outside data for the narrative engine, the exit planner and the perps book. Data rules:
  - store raw first: every response is saved as it arrived (table raw_feed, with the time we saw it), then parsed;
    so what the engine knew at any moment can be replayed later (point-in-time, no hindsight)
  - never fill silently: a source that fails, or returns nothing where it normally has data, is logged in data_gaps
  - official/public endpoints only: Dexscreener (no key), GeckoTerminal (no key, ~10 calls/min), news RSS feeds,
    Hyperliquid public info API, and X/Twitter only through the official API with your own X_BEARER_TOKEN
    (pay-per-use, about $0.005 per post read; capped by narrative.x.daily_read_budget). No scraping.
Every function returns plain dicts/lists and never raises for a network problem (it returns [] or None and logs a gap)."""
import os,re,json,time,gzip,logging,threading,urllib.request,urllib.parse,urllib.error,xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
log=logging.getLogger("bot")
UA={"user-agent":"zetrax-bot/1.0 (+paper trading research)","accept":"application/json"}
GT="https://api.geckoterminal.com/api/v2";GT_ACCEPT="application/json;version=20230302"
DS="https://api.dexscreener.com"
HL={"main":"https://api.hyperliquid.xyz","test":"https://api.hyperliquid-testnet.xyz"}
NEWS=[("coindesk","https://www.coindesk.com/arc/outboundfeeds/rss/"),("cointelegraph","https://cointelegraph.com/rss"),
      ("decrypt","https://decrypt.co/feed"),("theblock","https://www.theblock.co/rss.xml")]
DDL=["create table if not exists raw_feed(id integer primary key,ts real,source text,key text,payload text)",
     "create index if not exists raw_feed_src on raw_feed(source,ts)",
     "create table if not exists data_gaps(id integer primary key,ts real,source text,what text)"]
_lock=threading.Lock();_last={}
def _wait(host,gap):
    """Simple per-host rate limiter shared by every thread."""
    with _lock:
        t=_last.get(host,0);now=time.time()
        if now<t+gap:time.sleep(t+gap-now)
        _last[host]=time.time()
def http(url,headers=None,body=None,timeout=15,raw=False,gap=0.0):
    if gap:_wait(urllib.parse.urlparse(url).netloc,gap)
    h=dict(UA,**(headers or {}))
    if body is not None:h["content-type"]="application/json";body=json.dumps(body).encode()
    r=urllib.request.urlopen(urllib.request.Request(url,body,h),timeout=timeout);d=r.read()
    if r.headers.get("content-encoding")=="gzip":d=gzip.decompress(d)
    return d if raw else json.loads(d)
class Feeds:
    def __init__(s,db,cfg=None):
        s.db=db;s.cfg=(cfg or {}).get("narrative") or {};s.cache={}
        for q in DDL:db.c.execute(q)
        db.c.commit()
    # ---- raw store + gap log ---------------------------------------------------------------------------
    def raw(s,source,key,payload):
        try:
            s.db.c.execute("insert into raw_feed(ts,source,key,payload) values(?,?,?,?)",(time.time(),source,str(key)[:120],json.dumps(payload,separators=(",",":"))[:400000]))
            s.db.c.commit()
        except Exception as e:log.debug("raw store %s: %s",source,e)
    def prune(s,days=None):
        days=days or s.cfg.get("raw_keep_days",14)
        try:s.db.x("delete from raw_feed where ts<?",(time.time()-days*86400,));s.db.x("delete from data_gaps where ts<?",(time.time()-60*86400,))
        except Exception:pass
    def gap(s,source,what):
        log.info("data gap %s: %s",source,what)
        try:s.db.x("insert into data_gaps(ts,source,what) values(?,?,?)",(time.time(),source,str(what)[:300]))
        except Exception:pass
    def gaps(s,hours=24):
        return s.db.q("select ts,source,what from data_gaps where ts>? order by ts desc limit 50",(time.time()-hours*3600,))
    def _get(s,source,url,headers=None,body=None,key=None,store=True,gap=0.0,timeout=15):
        try:d=http(url,headers,body,timeout=timeout,gap=gap)
        except urllib.error.HTTPError as e:s.gap(source,f"HTTP {e.code} {url[:90]}");return None
        except Exception as e:s.gap(source,f"{type(e).__name__} {url[:90]}");return None
        if store:s.raw(source,key or url[:120],d)
        return d
    def _cached(s,k,ttl):
        v=s.cache.get(k)
        return v[1] if v and time.time()-v[0]<ttl else None
    # ---- GeckoTerminal (Solana pools, OHLCV) -------------------------------------------------------------
    def gt(s,path,params=None,key=None,store=True):
        u=GT+path+("?"+urllib.parse.urlencode(params) if params else "")
        return s._get("geckoterminal",u,{"accept":GT_ACCEPT},key=key or path,store=store,gap=6.5)
    @staticmethod
    def gt_pools(j):
        """GeckoTerminal pool list -> [{pool, mint, symbol, name, price, vol{}, ch{}, liq, created, buys_h1, sells_h1, buyers_h1}]."""
        if not j:return []
        inc={x["id"]:x.get("attributes") or {} for x in j.get("included") or []};out=[]
        for p in j.get("data") or []:
            a=p.get("attributes") or {};rel=(p.get("relationships") or {}).get("base_token",{}).get("data") or {}
            tid=rel.get("id","");mint=tid.split("_",1)[1] if "_" in tid else None;t=inc.get(tid,{})
            tx=(a.get("transactions") or {}).get("h1") or {}
            f=lambda d,k:float((d or {}).get(k) or 0)
            out.append(dict(pool=a.get("address"),mint=mint,symbol=(t.get("symbol") or (a.get("name") or "?").split(" /")[0]).upper(),name=t.get("name") or a.get("name"),
                price=float(a.get("base_token_price_usd") or 0),vol={k:f(a.get("volume_usd"),k) for k in("m5","h1","h6","h24")},
                ch={k:f(a.get("price_change_percentage"),k)/100 for k in("m5","h1","h6","h24")},liq=float(a.get("reserve_in_usd") or 0),
                created=a.get("pool_created_at"),buys_h1=tx.get("buys"),sells_h1=tx.get("sells"),buyers_h1=tx.get("buyers")))
        return out
    def gt_trending(s):return s.gt_pools(s.gt("/networks/solana/trending_pools",{"include":"base_token","page":1},key="trending"))
    def gt_new_pools(s):return s.gt_pools(s.gt("/networks/solana/new_pools",{"include":"base_token","page":1},key="new_pools"))
    def gt_top_pool(s,mint):
        k="pool:"+mint;v=s._cached(k,3600)
        if v:return v
        P=s.gt_pools(s.gt(f"/networks/solana/tokens/{mint}/pools",{"include":"base_token","page":1},key="pools:"+mint[:12],store=False))
        P=[p for p in P if p["mint"]==mint] or P
        if not P:return None
        best=max(P,key=lambda p:p["liq"])["pool"];s.cache[k]=(time.time(),best);return best
    def gt_ohlcv(s,pool,minutes=15,limit=60):
        """Closed candles oldest first: [(ts, o, h, l, c, v)]. GeckoTerminal returns the newest first, may include the
        candle still forming: it is dropped."""
        tf,agg=("minute",minutes) if minutes<60 else ("hour",minutes//60) if minutes<1440 else ("day",1)
        j=s.gt(f"/networks/solana/pools/{pool}/ohlcv/{tf}",{"aggregate":agg,"limit":limit,"currency":"usd"},key="ohlcv:"+pool[:12])
        L=((((j or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list")) or []
        rows=sorted((int(r[0]),float(r[1]),float(r[2]),float(r[3]),float(r[4]),float(r[5])) for r in L)
        now=time.time();return [r for r in rows if r[0]+minutes*60<=now]
    def atr_pct(s,mint,minutes=15,n=14):
        """Average true range of the coin's main pool as a share of its price (None if there is no usable history)."""
        k=f"atr:{mint}:{minutes}";v=s._cached(k,600)
        if v is not None:return v or None
        pool=s.gt_top_pool(mint);rows=s.gt_ohlcv(pool,minutes,max(40,n*3)) if pool else []
        a=atr_from_rows(rows,n);s.cache[k]=(time.time(),a or 0)
        if a is None:s.gap("geckoterminal",f"no {minutes}m candles for {mint[:8]}")
        return a
    # ---- Dexscreener (metas = market narratives, tokens, boosts) ---------------------------------------------
    def ds(s,path,key=None):return s._get("dexscreener",DS+path,key=key or path,gap=1.1)
    def ds_metas(s):
        """Dexscreener 'metas' (trending themes, e.g. AI agents, cats): the market's own narrative clusters."""
        out=[]
        for m in s.ds("/metas/trending/v1",key="metas") or []:
            ch=m.get("marketCapChange") or {}
            out.append(dict(slug=m.get("slug"),name=m.get("name"),desc=m.get("description"),mcap=m.get("marketCap"),liq=m.get("liquidity"),
                vol=m.get("volume"),tokens=m.get("tokenCount"),ch={k:(float(ch.get(k) or 0)/100) for k in("m5","h1","h6","h24")}))
        return [m for m in out if m["slug"]]
    @staticmethod
    def ds_pair(p):
        f=lambda d,k:float((d or {}).get(k) or 0);bt=p.get("baseToken") or {}
        return dict(mint=bt.get("address"),symbol=(bt.get("symbol") or "?").upper(),name=bt.get("name"),pair=p.get("pairAddress"),dex=p.get("dexId"),
            price=float(p.get("priceUsd") or 0),vol={k:f(p.get("volume"),k) for k in("m5","h1","h6","h24")},ch={k:f(p.get("priceChange"),k)/100 for k in("m5","h1","h6","h24")},
            liq=f(p.get("liquidity"),"usd"),txns=p.get("txns") or {},created=(p.get("pairCreatedAt") or 0)/1000,mcap=p.get("marketCap"),
            socials=[x.get("platform") or x.get("type") for x in ((p.get("info") or {}).get("socials") or [])],boosted=bool((p.get("boosts") or {}).get("active")))
    def ds_meta_pairs(s,slug):
        j=s.ds(f"/metas/meta/v1/{urllib.parse.quote(slug)}",key="meta:"+slug) or {}
        return [s.ds_pair(p) for p in j.get("pairs") or [] if p.get("chainId")=="solana"]
    def ds_boosts(s):
        out={}
        for ep in("/token-boosts/latest/v1","/token-boosts/top/v1"):
            for x in s.ds(ep,key=ep) or []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):out[x["tokenAddress"]]=max(out.get(x["tokenAddress"],0),float(x.get("totalAmount") or x.get("amount") or 0))
        return out
    def ds_tokens(s,mints):
        """Best (most liquid) Solana pair per mint, up to 30 mints per call."""
        out={};M=list(dict.fromkeys(m for m in mints if m))
        for i in range(0,len(M),30):
            for p in s.ds("/tokens/v1/solana/"+",".join(M[i:i+30]),key="tokens") or []:
                d=s.ds_pair(p)
                if d["mint"] and (d["mint"] not in out or d["liq"]>out[d["mint"]]["liq"]):out[d["mint"]]=d
        return out
    def ds_search(s,symbol):
        """Resolve a cashtag to the most liquid Solana pair with exactly that symbol (None if unsure)."""
        k="sym:"+symbol;v=s._cached(k,1800)
        if v is not None:return v or None
        j=s.ds("/latest/dex/search?q="+urllib.parse.quote(symbol),key="search:"+symbol) or {}
        P=[s.ds_pair(p) for p in j.get("pairs") or [] if p.get("chainId")=="solana" and ((p.get("baseToken") or {}).get("symbol") or "").upper()==symbol.upper()]
        best=max(P,key=lambda p:p["liq"]) if P else None;s.cache[k]=(time.time(),best or 0);return best
    # ---- news (RSS) -------------------------------------------------------------------------------------
    def news(s):
        out=[]
        for src,u in s.cfg.get("news_feeds") or NEWS:
            try:x=http(u,{"accept":"application/rss+xml, application/xml, text/xml"},raw=True,timeout=15,gap=1.0)
            except Exception as e:s.gap("news",f"{src}: {type(e).__name__}");continue
            items=parse_rss(x,src)
            if not items:s.gap("news",f"{src}: feed had no items");continue
            s.raw("news",src,items[:40]);out+=items
        return out
    # ---- X / Twitter (official API, your key, hard daily budget) ----------------------------------------
    def x_enabled(s):return bool(os.environ.get("X_BEARER_TOKEN","").strip()) and (s.cfg.get("x") or {}).get("enabled",True)
    def x_budget_left(s):
        d=time.strftime("%Y-%m-%d",time.gmtime());u=s.db.kv_get("x:reads") or {}
        return max(0,int((s.cfg.get("x") or {}).get("daily_read_budget",300))-int(u.get(d,0)))
    def x_search(s,name,query,want=100):
        """Recent search (last 7 days) for new posts since the last call of this query. Counts every post read against
        the daily budget. -> list of posts {id, ts, text, author, followers, account_age_days, engagement, cashtags, hashtags}."""
        if not s.x_enabled():return []
        left=s.x_budget_left()
        if left<10:s.gap("x","daily read budget used");return []
        n=max(10,min(100,want,left));since=s.db.kv_get("x:since:"+name)
        p={"query":query,"max_results":n,"tweet.fields":"created_at,public_metrics,author_id,entities,lang","expansions":"author_id",
           "user.fields":"created_at,public_metrics,verified,username"}
        if since:p["since_id"]=since
        tok=os.environ.get("X_BEARER_TOKEN","").strip()
        j=s._get("x","https://api.x.com/2/tweets/search/recent?"+urllib.parse.urlencode(p),{"authorization":"Bearer "+tok},key="x:"+name,timeout=20)
        if j is None:return []
        posts=parse_x(j);d=time.strftime("%Y-%m-%d",time.gmtime());u=s.db.kv_get("x:reads") or {}
        u={k:v for k,v in u.items() if k>=time.strftime("%Y-%m-%d",time.gmtime(time.time()-30*86400))};u[d]=u.get(d,0)+len(posts);s.db.kv_set("x:reads",u)
        nid=((j.get("meta") or {}).get("newest_id"))
        if nid:s.db.kv_set("x:since:"+name,nid)
        return posts
    # ---- Hyperliquid public info (perps data) -------------------------------------------------------------
    def hl(s,body,net="main",store=False):
        return s._get("hyperliquid",HL[net]+"/info",body=body,key=body.get("type"),store=store,gap=0.25,timeout=15)
# ---- parsers (pure, unit-tested) ------------------------------------------------------------------------------
def atr_from_rows(rows,n=14):
    """Wilder ATR of the last n candles as a share of the last close. rows: [(ts,o,h,l,c,v)] oldest first."""
    if len(rows)<n+1:return None
    tr=[]
    for i in range(1,len(rows)):
        h,l,pc=rows[i][2],rows[i][3],rows[i-1][4];tr.append(max(h-l,abs(h-pc),abs(l-pc)))
    a=sum(tr[:n])/n
    for x in tr[n:]:a=(a*(n-1)+x)/n
    c=rows[-1][4];return a/c if c>0 else None
def parse_rss(x,src):
    try:root=ET.fromstring(x)
    except ET.ParseError:return []
    out=[]
    for it in root.iter("item"):
        t=(it.findtext("title") or "").strip();l=(it.findtext("link") or "").strip();d=it.findtext("pubDate")
        try:ts=parsedate_to_datetime(d).timestamp() if d else time.time()
        except Exception:ts=time.time()
        desc=re.sub(r"<[^>]+>"," ",it.findtext("description") or "")[:400]
        if t:out.append(dict(source=src,id=l or t,title=t,ts=ts,summary=" ".join(desc.split())))
    return out
def parse_x(j):
    U={u["id"]:u for u in ((j.get("includes") or {}).get("users") or [])};out=[]
    for t in j.get("data") or []:
        u=U.get(t.get("author_id"),{});pm=t.get("public_metrics") or {};um=u.get("public_metrics") or {}
        try:age=(time.time()-_iso(u.get("created_at")))/86400 if u.get("created_at") else None
        except Exception:age=None
        ent=t.get("entities") or {}
        out.append(dict(id=t.get("id"),ts=_iso(t.get("created_at")) if t.get("created_at") else time.time(),text=t.get("text") or "",author=u.get("username") or t.get("author_id"),
            followers=um.get("followers_count",0),account_age_days=age,verified=bool(u.get("verified")),
            engagement=pm.get("like_count",0)+2*pm.get("retweet_count",0)+pm.get("reply_count",0)+2*pm.get("quote_count",0),
            cashtags=[c.get("tag","").upper() for c in ent.get("cashtags") or []],hashtags=[h.get("tag","").lower() for h in ent.get("hashtags") or []]))
    return out
def _iso(s):
    import datetime as dt
    return dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()
