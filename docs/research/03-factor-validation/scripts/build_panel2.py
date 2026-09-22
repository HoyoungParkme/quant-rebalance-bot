import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
px=pickle.load(open("bt/px_all.pkl","rb")); pxd=pickle.load(open("bt/px_del.pkl","rb")); px={**px,**pxd}
L=pd.read_csv("bt/listing.csv",dtype={"Code":str}).set_index("Code"); imp=pd.read_csv("bt/implied_shares.csv",dtype={"code":str}).set_index("code")
DL=pd.read_csv("bt/delisted.csv",dtype={"code":str}).set_index("code")
mac=pickle.load(open("bt/macro.pkl","rb")); cal=mac["KOSPI"].index
C=pd.DataFrame({c:p.Close for c,p in px.items()}).reindex(cal); V=pd.DataFrame({c:p.Volume for c,p in px.items()}).reindex(cal).fillna(0)
first=C.notna().cummax(); C=C.where(C>0).ffill().where(first)
lastrow={c:p.index[-1] for c,p in px.items()}
for c in pxd: C.loc[C.index>lastrow[c],c]=np.nan                 # 폐지 후에는 가격 없음
CT=C.where(V>0); EXIT=CT.bfill(); LASTT=CT.ffill()               # 거래된 날의 종가만
sh=L.Stocks.reindex(C.columns).astype(float); sh=sh.fillna(imp.sh.reindex(C.columns)); print("주식 수 없음:",int(sh.isna().sum()),"/",len(sh))
MC=C*sh; TV=C*V; R=C.pct_change()
fs=pd.read_csv("bt/fs.csv",dtype={"code":str},parse_dates=["fy_end","avail"]).sort_values(["code","fy_end"]); fs["op_g"]=fs.groupby("code").op.pct_change().where(fs.groupby("code").op.shift()>0)
me=pd.Series(cal,index=cal).resample("M").last().loc["2019-12":"2026-08"].values; rows=[]
for j,t in enumerate(me[:-1]):
    t=pd.Timestamp(t); t1=pd.Timestamp(me[j+1]); i=cal.get_loc(t)
    if i<252: continue
    c=C.iloc[i]; mc=MC.iloc[i]; d=pd.DataFrame({"close":c,"mcap":mc,"tv20":TV.iloc[i-19:i+1].mean(),"traded":V.iloc[i]>0})
    ex=EXIT.loc[t1]; lt=LASTT.iloc[-1].where(pd.Series({k:(CT[k].last_valid_index() is not None and CT[k].last_valid_index()>t) for k in []}))  # placeholder
    lastp=LASTT.iloc[-1]; lastd=CT.apply(lambda s:s.last_valid_index())
    ex=ex.fillna(lastp.where(lastd>t))                          # t1 이후 거래가 없으면 마지막 거래 종가로 청산
    d["fwd_naive"]=(C.loc[t1]/c-1); d["fwd"]=(ex/c-1).fillna(0).clip(-1,3)
    d["MOM12_1"]=C.iloc[i-21]/C.iloc[i-252]-1; d["HIGH52"]=c/C.iloc[i-251:i+1].max(); d["MOM3"]=c/C.iloc[i-63]-1
    d["VOL60"]=R.iloc[i-59:i+1].std(); d["VOLSURGE"]=TV.iloc[i-4:i+1].mean()/TV.iloc[i-59:i+1].mean(); d["TURN20"]=TV.iloc[i-19:i+1].sum()/mc
    f=fs[fs.avail<=t].drop_duplicates("code",keep="last").set_index("code"); f=f[(t-f.fy_end).dt.days<=500]
    d["EP"]=f.ni_use.reindex(d.index)/mc; d["BP"]=f.equity_use.reindex(d.index)/mc; d["SP"]=f.rev.reindex(d.index)/mc
    d["ROE"]=(f.ni_use/f.equity_use.where(f.equity_use>0)).reindex(d.index); d["OPG"]=f.op_g.reindex(d.index).clip(-1,5)
    d["date"]=t; d["delisted"]=d.index.isin(pxd.keys()); d["del_type"]=DL.type.reindex(d.index)
    d=d[(d.close>=1000)&(d.tv20>=5e8)&d.traded&(d.mcap>=5e10)]
    rows.append(d.reset_index().rename(columns={"index":"code"}))
P=pd.concat(rows); P.to_pickle("bt/panel2.pkl")
print("panel2",P.shape,"| 월평균 종목",int(P.groupby("date").size().mean()),"| 폐지 종목 관측:",int(P.delisted.sum()),"(종목 %d개)"%P[P.delisted].code.nunique())
print(P[P.delisted].groupby("del_type").code.nunique().to_string())
