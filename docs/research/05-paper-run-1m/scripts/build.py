"""모의 운용용 월말 패널. 각 월말 t의 지표는 t 시점까지 공개된 정보만 사용한다."""
import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
px={**pickle.load(open("bt/px_all.pkl","rb")),**pickle.load(open("bt/px_del.pkl","rb"))}; pxd=pickle.load(open("bt/px_del.pkl","rb"))
L=pd.read_csv("bt/listing.csv",dtype={"Code":str}).set_index("Code"); imp=pd.read_csv("bt/implied_shares.csv",dtype={"code":str}).set_index("code")
mac=pickle.load(open("bt/macro.pkl","rb")); cal=mac["KOSPI"].index
O=pd.DataFrame({c:p.Open for c,p in px.items()}).reindex(cal); C=pd.DataFrame({c:p.Close for c,p in px.items()}).reindex(cal); V=pd.DataFrame({c:p.Volume for c,p in px.items()}).reindex(cal).fillna(0)
first=C.notna().cummax(); C=C.where(C>0).ffill().where(first)
for c,p in pxd.items(): C.loc[C.index>p.index[-1],c]=np.nan
pickle.dump((O,C,V),open("sim/ocv.pkl","wb"))
sh=L.Stocks.reindex(C.columns).astype(float).fillna(imp.sh.reindex(C.columns)); MC=C*sh; TV=C*V; R=C.pct_change()
fs=pd.read_csv("bt/fs.csv",dtype={"code":str},parse_dates=["fy_end","avail"]).sort_values(["code","fy_end"]); fs["op_g"]=fs.groupby("code").op.pct_change().where(fs.groupby("code").op.shift()>0)
Q=pd.read_pickle("bt/qop.pkl"); Q["avail"]=Q.qend+pd.to_timedelta(Q.lag,unit="D"); Q=Q.sort_values("avail")
CT=C.where(V>0); EXIT=CT.bfill(); lastp=CT.ffill().iloc[-1]; lastd=CT.apply(lambda s:s.last_valid_index())
me=list(pd.Series(cal,index=cal).resample("M").last().loc["2019-12":"2026-08"].values); rows=[]
for j,t in enumerate(me):
    t=pd.Timestamp(t); i=cal.get_loc(t)
    if i<252: continue
    c=C.iloc[i]; mc=MC.iloc[i]; d=pd.DataFrame({"close":c,"mcap":mc,"tv20":TV.iloc[i-19:i+1].mean(),"traded":V.iloc[i]>0})
    if j+1<len(me):
        t1=pd.Timestamp(me[j+1]); ex=EXIT.loc[t1].fillna(lastp.where(lastd>t)); d["fwd"]=(ex/c-1).fillna(0).clip(-1,3)
    else: d["fwd"]=np.nan
    d["MOM12_1"]=C.iloc[i-21]/C.iloc[i-252]-1; d["HIGH52"]=c/C.iloc[i-251:i+1].max(); d["MOM3"]=c/C.iloc[i-63]-1
    d["VOL60"]=R.iloc[i-59:i+1].std(); d["VOLSURGE"]=TV.iloc[i-4:i+1].mean()/TV.iloc[i-59:i+1].mean(); d["TURN20"]=TV.iloc[i-19:i+1].sum()/mc
    f=fs[fs.avail<=t].drop_duplicates("code",keep="last").set_index("code"); f=f[(t-f.fy_end).dt.days<=500]
    d["EP"]=f.ni_use.reindex(d.index)/mc; d["BP"]=f.equity_use.reindex(d.index)/mc; d["SP"]=f.rev.reindex(d.index)/mc
    d["ROE"]=(f.ni_use/f.equity_use.where(f.equity_use>0)).reindex(d.index); d["OPG"]=f.op_g.reindex(d.index).clip(-1,5)
    q=Q[(Q.avail<=t)&((t-Q.avail).dt.days<=150)].drop_duplicates("code",keep="last").set_index("code"); d["OPG_Q"]=q.g.reindex(d.index)
    d["date"]=t; d=d[(d.close>=1000)&(d.tv20>=5e8)&d.traded&(d.mcap>=5e10)]
    rows.append(d.reset_index().rename(columns={"index":"code"}))
P=pd.concat(rows); P.to_pickle("sim/panel3.pkl"); print("panel3",P.shape,P.date.min().date(),"~",P.date.max().date())
