import sqlite3,time,json
S="""create table if not exists kv(k text primary key,v text);
create table if not exists seen(sig text primary key,ts real);
create table if not exists decisions(ts real,trader text,wallet text,selected int,reason text,metrics text);
create table if not exists positions(id integer primary key,trader text,mint text,tokens real,cost real,opened real,status text,closed real,realized real default 0,open_sig text);
create table if not exists trades(ts real,trader text,mint text,side text,usd real,tokens real,price real,pnl real,sig text,note text);
create table if not exists equity(ts real,equity real,cash real);
create table if not exists events(sig text primary key,wallet text,t real,payload text);"""
class DB:
    def __init__(s,path):
        s.c=sqlite3.connect(path,timeout=30,check_same_thread=False);s.c.executescript(S);s.c.commit()
    def kv_get(s,k,d=None):
        r=s.c.execute("select v from kv where k=?",(k,)).fetchone();return json.loads(r[0]) if r else d
    def kv_set(s,k,v): s.c.execute("insert or replace into kv values(?,?)",(k,json.dumps(v)));s.c.commit()
    def seen(s,sig):
        if s.c.execute("select 1 from seen where sig=?",(sig,)).fetchone(): return True
        s.c.execute("insert into seen values(?,?)",(sig,time.time()));s.c.commit();return False
    def q(s,sql,a=()): return s.c.execute(sql,a).fetchall()
    def x(s,sql,a=()): cur=s.c.execute(sql,a);s.c.commit();return cur.lastrowid
