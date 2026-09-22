import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
P=pd.read_pickle("bt/panel.pkl"); mac=pickle.load(open("bt/macro.pkl","rb"))
pd.set_option("display.width",250); pd.set_option("display.float_format",lambda v:f"{v:,.1f}")
K=mac["KOSPI"].resample("M").last(); trend=(K>K.rolling(10).mean())
SC={"S1 저평가(EP+BP)":{"EP":1,"BP":1},
    "S2 저평가+우량(EP,BP,ROE,OPG)":{"EP":1,"BP":1,"ROE":1,"OPG":1},
    "S3 모멘텀(12개월+52주고가)":{"MOM12_1":1,"HIGH52":1},
    "S4 수급추종(외국인+기관 20일)":{"FRG20":1,"ORG20":1},
    "S5 거래량급증 추격(급증+3개월상승)":{"VOLSURGE":1,"MOM3":1},
    "S6 종합(저평가+우량+저변동)":{"EP":1,"BP":1,"ROE":1,"OPG":1,"VOL60":-1,"TURN20":-1}}
N=20; COST=0.003
def run(w,filt=False):
    prev=set(); rets={}
    for t,g in P.groupby("date"):
        g=g.dropna(subset=list(w)); 
        if len(g)<100: continue
        s=sum(sg*g[f].rank(pct=True) for f,sg in w.items()); pick=set(g.loc[s.nlargest(N).index,"code"])
        r=g[g.code.isin(pick)].fwd.mean(); to=1-len(pick&prev)/N if prev else 1
        tm=t+pd.offsets.MonthEnd(0)
        if filt and not bool(trend.get(tm,True)): r=0.0; to=(1 if prev else 0); pick=set()
        rets[t]=r-to*COST; prev=pick
    return pd.Series(rets)
def stats(r):
    eq=(1+r).cumprod(); yrs=len(r)/12; mdd=(eq/eq.cummax()-1).min()
    return {"연복리%":(eq.iloc[-1]**(1/yrs)-1)*100,"최대낙폭%":mdd*100,"샤프":r.mean()/r.std()*np.sqrt(12),"월승률%":(r>0).mean()*100}
res={}
for n,w in SC.items():
    if all(f in P for f in w): res[n]=run(w)
res["S7 종합+추세필터(하락장 현금)"]=run(SC["S6 종합(저평가+우량+저변동)"],True)
res["기준: 전종목 동일가중"]=P.groupby("date").fwd.mean()
kr=K.pct_change().shift(-1); kr.index=kr.index; b=res["기준: 전종목 동일가중"]; res["기준: KOSPI"]=pd.Series([kr.get(t+pd.offsets.MonthEnd(0),np.nan) for t in b.index],index=b.index)
rows=[]
for n,r in res.items():
    r=r.dropna(); row={"시나리오":n,**stats(r)}
    row["검증전반 20-23 연복리%"]=stats(r[:"2023-12"])["연복리%"]; row["검증후반 24-26 연복리%"]=stats(r["2024-01":])["연복리%"]
    for y in range(2020,2027): 
        ry=r[str(y)]; row[str(y)]=((1+ry).prod()-1)*100 if len(ry) else np.nan
    rows.append(row)
T=pd.DataFrame(rows).set_index("시나리오"); T.to_csv("bt/scenario_results.csv"); print(T.to_string())
pickle.dump(res,open("bt/scenario_series.pkl","wb"))
