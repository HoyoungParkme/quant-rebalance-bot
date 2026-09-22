import requests, pandas as pd, pickle, time, sys, os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
codes=open("bt/flow_universe.txt").read().split()
done={}
if os.path.exists("bt/flows.pkl"): done=pickle.load(open("bt/flows.pkl","rb"))
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
def num(s):
    try: return float(str(s).replace(",","").replace("+","").replace("%",""))
    except Exception: return 0.0
def get(c):
    rows=[]; biz="20260918"; prev=None; pages=0
    while biz>="20191201":
        for k in range(4):
            try:
                r=S.get(f"https://m.stock.naver.com/api/stock/{c}/trend?pageSize=60&bizdate={biz}",timeout=8); d=r.json(); break
            except Exception: time.sleep(1.5*(k+1)); d=None
        if not d or not isinstance(d,list): break
        rows+=d
        last=d[-1]["bizdate"]; pages+=1
        if (prev is not None and last>=prev) or pages>40: break
        prev=last
        if len(d)<60: break
        biz=(datetime.strptime(last,"%Y%m%d")-timedelta(days=1)).strftime("%Y%m%d")
    if not rows: return c,None
    df=pd.DataFrame([{"date":x["bizdate"],"frg":num(x["foreignerPureBuyQuant"]),"org":num(x["organPureBuyQuant"]),"ind":num(x["individualPureBuyQuant"]),"frg_ratio":num(x["foreignerHoldRatio"])} for x in rows]).drop_duplicates("date")
    df["date"]=pd.to_datetime(df.date); return c,df.set_index("date").sort_index()
todo=[c for c in codes if c not in done]; print("todo",len(todo),flush=True); t=time.time(); n=0
with ThreadPoolExecutor(10) as ex:
    for c,df in ex.map(get,todo):
        n+=1
        if df is not None: done[c]=df
        if n%100==0:
            pickle.dump(done,open("bt/flows.pkl","wb")); print(n,len(done),"%.0fs"%(time.time()-t),flush=True)
pickle.dump(done,open("bt/flows.pkl","wb")); print("DONE",len(done),"%.0fs"%(time.time()-t),flush=True)
