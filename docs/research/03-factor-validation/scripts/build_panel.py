import pandas as pd, numpy as np, pickle, os, warnings; warnings.filterwarnings("ignore")
px=pickle.load(open("bt/px_all.pkl","rb")); L=pd.read_csv("bt/listing.csv",dtype={"Code":str}).set_index("Code")
mac=pickle.load(open("bt/macro.pkl","rb")); cal=mac["KOSPI"].index
C=pd.DataFrame({c:p.Close for c,p in px.items()}).reindex(cal); V=pd.DataFrame({c:p.Volume for c,p in px.items()}).reindex(cal).fillna(0)
listed=C.notna().cummax()                      # 상장 이후만
C=C.where(C>0).ffill().where(listed)
TV=C*V; sh=L.Stocks.reindex(C.columns); MC=C*sh   # 시총 근사: 현재 주식수 × 수정주가
R=C.pct_change()
fs=pd.read_csv("bt/fs.csv",dtype={"code":str},parse_dates=["fy_end","avail"]).sort_values(["code","fy_end"])
fs["op_g"]=fs.groupby("code").op.pct_change().where(fs.groupby("code").op.shift()>0)
fl=pickle.load(open("bt/flows.pkl","rb")) if os.path.exists("bt/flows.pkl") else {}
F={k:pd.DataFrame({c:d[k] for c,d in fl.items()}).reindex(cal) for k in ["frg","org","ind","frg_ratio"]} if fl else {}
me=pd.Series(cal,index=cal).resample("M").last().loc["2019-12":"2026-08"].values
rows=[]
for j,t in enumerate(me[:-1]):
    t=pd.Timestamp(t); t1=pd.Timestamp(me[j+1]); i=cal.get_loc(t)
    if i<252: continue
    c=C.iloc[i]; mc=MC.iloc[i]
    d=pd.DataFrame({"close":c,"mcap":mc,"tv20":TV.iloc[i-19:i+1].mean()})
    d["fwd"]=(C.loc[t1]/c-1).clip(-0.9,3)
    d["MOM12_1"]=C.iloc[i-21]/C.iloc[i-252]-1; d["MOM6_1"]=C.iloc[i-21]/C.iloc[i-126]-1; d["MOM3"]=c/C.iloc[i-63]-1
    d["REV1"]=c/C.iloc[i-21]-1; d["VOL60"]=R.iloc[i-59:i+1].std()
    d["HIGH52"]=c/C.iloc[i-251:i+1].max()
    d["VOLSURGE"]=TV.iloc[i-4:i+1].mean()/TV.iloc[i-59:i+1].mean(); d["TURN20"]=TV.iloc[i-19:i+1].sum()/mc
    d["SIZE"]=np.log(mc)
    f=fs[fs.avail<=t].drop_duplicates("code",keep="last").set_index("code")
    f=f[(t-f.fy_end).dt.days<=500]
    d["EP"]=f.ni_use.reindex(d.index)/mc; d["BP"]=f.equity_use.reindex(d.index)/mc
    d["ROE"]=(f.ni_use/f.equity_use.where(f.equity_use>0)).reindex(d.index); d["OPG"]=f.op_g.reindex(d.index).clip(-1,5)
    d["SP"]=f.rev.reindex(d.index)/mc
    for k in ["frg","org","ind"]:
        if F:
            amt=(F[k]*C.reindex(columns=F[k].columns))
            d[k.upper()+"20"]=amt.iloc[i-19:i+1].sum().reindex(d.index)/mc; d[k.upper()+"60"]=amt.iloc[i-59:i+1].sum().reindex(d.index)/mc
    if F: d["FRGR60"]=(F["frg_ratio"].iloc[i]-F["frg_ratio"].iloc[i-60]).reindex(d.index)
    d["date"]=t; d["market"]=L.Market.reindex(d.index)
    d=d[(d.close>=1000)&(d.tv20>=5e8)&(d.mcap>=5e10)&d.fwd.notna()]
    rows.append(d.reset_index().rename(columns={"index":"code"}))
P=pd.concat(rows); P.to_pickle("bt/panel.pkl")
print("panel",P.shape,"months",P.date.nunique(),"avg stocks/month %.0f"%P.groupby("date").size().mean())
print(P.notna().mean().round(2).to_string())
