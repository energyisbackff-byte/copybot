"""SOL/USD 15-minute candles for Trend Rider. Coinbase Exchange public API (no key) with Kraken as fallback for recent
candles. Stored in the `candles` table so the backtest and the live strategy read exactly the same data.
Only CLOSED candles are ever used: a candle starting at ts is closed once ts+900 <= now."""
import json,time,logging,urllib.request,datetime as dt
log=logging.getLogger("bot")
SYM="SOL-USD";TF=900
CB="https://api.exchange.coinbase.com/products/SOL-USD/candles"
KR="https://api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=15"
def _get(u,timeout=20):
    return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"user-agent":"copybot-trend","accept":"application/json"}),timeout=timeout))
def _iso(t):return dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def fetch_coinbase(start,end,tries=4,timeout=20):
    """Closed 15m candles in [start, end), oldest first. Coinbase returns at most 300 per call, newest first."""
    out=[];t=start
    while t<end:
        e=min(end,t+300*TF)
        for a in range(tries):
            try:rows=_get(f"{CB}?granularity={TF}&start={_iso(t)}&end={_iso(e)}",timeout);break
            except Exception as ex:
                if a==tries-1:raise
                time.sleep(1.5*(a+1))
        out+=[(int(r[0]),float(r[3]),float(r[2]),float(r[1]),float(r[4]),float(r[5])) for r in rows if start<=int(r[0])<end]
        t=e;time.sleep(0.25)
    return sorted(set(out))
def fetch_kraken():
    r=_get(KR,8)
    if r.get("error"):raise RuntimeError(str(r["error"]))
    rows=next(v for k,v in r["result"].items() if k!="last")
    return [(int(x[0]),float(x[1]),float(x[2]),float(x[3]),float(x[4]),float(x[6])) for x in rows]
def store(db,rows):
    db.c.executemany("insert or replace into candles(sym,tf,ts,o,h,l,c,v) values(?,?,?,?,?,?,?,?)",[(SYM,TF)+tuple(r) for r in rows]);db.c.commit()
def last_ts(db):return db.q("select max(ts) from candles where sym=? and tf=?",(SYM,TF))[0][0]
def sync(db,need_days=10,now=None):
    """Bring stored candles up to the last closed 15m candle. Returns the newest closed candle start time."""
    now=now or time.time();closed_end=int(now//TF*TF)       # candles starting before this are closed
    lt=last_ts(db);start=max(lt+TF if lt else 0,closed_end-need_days*86400)
    if start>=closed_end:return lt
    try:rows=fetch_coinbase(start,closed_end,tries=1,timeout=8)    # live path: fail fast, the main loop must not stall
    except Exception as e:
        log.warning("coinbase candles failed (%s), trying kraken",e)
        rows=[r for r in fetch_kraken() if start<=r[0]<closed_end]
    store(db,rows);return last_ts(db)
def backfill(db,days,progress=None):
    """Fill history for the backtest (about 70 requests per 2 months). Skips ranges already stored."""
    end=int(time.time()//TF*TF);start=end-int(days*86400);start-=start%TF
    have={t for (t,) in db.q("select ts from candles where sym=? and tf=? and ts>=?",(SYM,TF,start))}
    t=start;chunk=300*TF*20
    while t<end:
        e=min(end,t+chunk);need=(e-t)//TF
        if sum(1 for x in range(t,e,TF) if x in have)<need*0.98:
            store(db,fetch_coinbase(t,e))
            if progress:progress((e-start)/(end-start))
        t=e
    return db.q("select count(*) from candles where sym=? and tf=? and ts>=?",(SYM,TF,start))[0][0]
def load(db,since=0):
    return db.q("select ts,o,h,l,c,v from candles where sym=? and tf=? and ts>=? order by ts",(SYM,TF,since))
