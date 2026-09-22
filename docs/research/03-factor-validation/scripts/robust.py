import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
P0=pd.read_pickle("bt/panel.pkl"); mac=pickle.load(open("bt/macro.pkl","rb")); K=mac["KOSPI"].resample("M").last(); trend=(K>K.rolling(10).mean())
W={"EP":1,"BP":1,"ROE":1,"OPG":1,"VOL60":-1,"TURN20":-1}
def run(P,N,cost,filt=False,skipworst=False):
    prev=set(); rets={}
    for t,g in P.groupby("date"):
        g=g.dropna(subset=list(W))
        if len(g)<100: continue
        s=sum(sg*g[f].rank(pct=True) for f,sg in W.items()); pick=set(g.loc[s.nlargest(N).index,"code"])
        r=g[g.code.isin(pick)].fwd.mean(); to=1-len(pick&prev)/N if prev else 1
        if filt and not bool(trend.get(t+pd.offsets.MonthEnd(0),True)): r=0.0; pick=set(); to=1 if prev else 0
        rets[t]=r-to*cost; prev=pick
    r=pd.Series(rets); eq=(1+r).cumprod()
    return {"연복리%":(eq.iloc[-1]**(12/len(r))-1)*100,"최대낙폭%":(eq/eq.cummax()-1).min()*100,"샤프":r.mean()/r.std()*np.sqrt(12),"전반20-23%":((1+r[:"2023-12"]).prod()**(12/len(r[:"2023-12"]))-1)*100,"후반24-26%":((1+r["2024-01":]).prod()**(12/len(r["2024-01":]))-1)*100}
rows=[]
for lab,P in [("기본 유니버스(시총500억+, 거래대금5억+)",P0),("대형·중형만(시총3000억+, 거래대금20억+)",P0[(P0.mcap>=3e11)&(P0.tv20>=2e9)])]:
    for N in [10,20,30,50]:
        rows.append({"유니버스":lab,"종목수":N,"비용":"0.3%",**run(P,N,0.003)})
    rows.append({"유니버스":lab,"종목수":20,"비용":"1.0%(체결오차 포함)",**run(P,20,0.010)})
    rows.append({"유니버스":lab,"종목수":20,"비용":"0.3%+추세필터",**run(P,20,0.003,True)})
pd.set_option("display.width",220); pd.set_option("display.float_format",lambda v:f"{v:,.1f}")
T=pd.DataFrame(rows); T.to_csv("bt/robust_results.csv",index=False); print(T.to_string(index=False))
print("\n종합 시나리오 월평균 교체율:"); 
