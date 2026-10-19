import sqlite3,time,json
# v1 tables: still created so an older version (after /rollback) finds exactly what it expects.
S="""create table if not exists kv(k text primary key,v text);
create table if not exists seen(sig text primary key,ts real);
create table if not exists decisions(ts real,trader text,wallet text,selected int,reason text,metrics text);
create table if not exists positions(id integer primary key,trader text,mint text,tokens real,cost real,opened real,status text,closed real,realized real default 0,open_sig text);
create table if not exists trades(ts real,trader text,mint text,side text,usd real,tokens real,price real,pnl real,sig text,note text);
create table if not exists equity(ts real,equity real,cash real);
create table if not exists events(sig text primary key,wallet text,t real,payload text);"""
# v2 "books" tables: every row carries its book (copy, consensus, trend, trend-live). New code only writes here, so a
# rollback to v1 code never sees rows it cannot understand, and v1 rows are never touched.
S2="""create table if not exists b_positions(id integer primary key,book text,trader text,mint text,tokens real,cost real,opened real,
  status text,closed real,realized real default 0,open_sig text,meta text);
create index if not exists b_positions_open on b_positions(book,status);
create table if not exists b_trades(id integer primary key,ts real,book text,trader text,mint text,side text,usd real,tokens real,
  price real,pnl real,sig text,note text);
create index if not exists b_trades_book on b_trades(book,ts);
create table if not exists b_fills(id integer primary key,ts real,book text,sig text,trader text,mint text,side text,status text,reason text,
  trader_block_ts real,detect_ts real,fill_ts real,detect_ms real,total_ms real,trader_px real,quote_px real,our_px real,
  slip_bps real,impact real,size_usd real,fee_usd real,live int default 0,txsig text);
create index if not exists b_fills_book on b_fills(book,ts);
create table if not exists b_equity(ts real,book text,equity real,cash real);
create index if not exists b_equity_book on b_equity(book,ts);
create table if not exists b_signals(id integer primary key,ts real,book text,source text,mint text,side text,decision text,
  reason text,size_usd real,features text);
create index if not exists b_signals_book on b_signals(book,ts);
create table if not exists activity(id integer primary key,ts real,kind text,data text);
create table if not exists candles(sym text,tf integer,ts integer,o real,h real,l real,c real,v real,primary key(sym,tf,ts));
create table if not exists spot_intents(id integer primary key,ts real,book text,kind text,mint text,paper_pid integer,frac real,reason text,
  note text,status text,tries integer default 0,next_ts real default 0,size real,result text,done_ts real);
create index if not exists spot_intents_open on spot_intents(status);"""
class DB:
    def __init__(s,path):
        s.c=sqlite3.connect(path,timeout=30,check_same_thread=False)
        try:s.c.execute("pragma journal_mode=wal")   # several threads write (main loop, narrative, perps): readers never block writers
        except sqlite3.DatabaseError:pass
        s.c.executescript(S);s.c.executescript(S2);s.c.commit()
    def kv_get(s,k,d=None):
        r=s.c.execute("select v from kv where k=?",(k,)).fetchone();return json.loads(r[0]) if r else d
    def kv_set(s,k,v): s.c.execute("insert or replace into kv values(?,?)",(k,json.dumps(v)));s.c.commit()
    def seen(s,sig):
        if s.c.execute("select 1 from seen where sig=?",(sig,)).fetchone(): return True
        s.c.execute("insert into seen values(?,?)",(sig,time.time()));s.c.commit();return False
    def q(s,sql,a=()): return s.c.execute(sql,a).fetchall()
    def x(s,sql,a=()): cur=s.c.execute(sql,a);s.c.commit();return cur.lastrowid
    def ins(s,table,**k):
        """Insert by column name (never positional), so adding a column can never break an older insert."""
        cols=list(k);return s.x(f"insert into {table}({','.join(cols)}) values({','.join('?'*len(cols))})",[k[c] for c in cols])
