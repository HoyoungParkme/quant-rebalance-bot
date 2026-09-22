"""유연한 모델이 더 나은가. 모든 방식은 매년 1월, 그 전까지의 데이터만으로 학습하고 그 해에 적용한다."""
import pandas as pd, numpy as np, warnings; warnings.filterwarnings("ignore")
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
P=pd.read_pickle("sim/panel3.pkl"); P=P[P.fwd.notna()].copy()
F=["EP","BP","SP","ROE","OPG","OPG_Q","MOM12_1","MOM3","HIGH52","VOL60","TURN20","VOLSURGE"]
for f in F: P["r_"+f]=P.groupby("date")[f].rank(pct=True)
RF=["r_"+f for f in F]; P["y"]=P.fwd-P.groupby("date").fwd.transform("mean"); P["y"]=P.y.clip(-0.3,0.3)
def tstats(D):
    out={}
    for f in F:
        ls=[]
        for t,g in D.dropna(subset=[f]).groupby("date"):
            if len(g)<100: continue
            b=pd.qcut(g[f].rank(method="first"),5,labels=False); r=g.groupby(b).fwd.mean(); ls.append(r[4]-r[0])
        ls=pd.Series(ls); out[f]=ls.mean()/ls.std()*np.sqrt(len(ls))
    return out
STATIC=None; preds=[]; chosen={}
for Y in range(2022,2027):
    tr=P[P.date<pd.Timestamp(f"{Y-1}-12-01")]; te=P[(P.date>=pd.Timestamp(f"{Y-1}-12-01"))&(P.date<pd.Timestamp(f"{Y}-12-01"))].copy()
    if len(te)==0: continue
    ts=tstats(tr); sel={f:(1 if v>0 else -1) for f,v in ts.items() if abs(v)>=2.0}; chosen[Y]=sel
    if STATIC is None: STATIC=sel                                   # 첫 해(2021년까지 데이터)에 고른 규칙을 끝까지 고정
    sc=lambda W: sum(sg*te["r_"+f].fillna(0.5) for f,sg in W.items())
    te["고정 규칙"]=sc(STATIC); te["매년 재선정"]=sc(sel)
    X=tr[RF].fillna(0.5); te["선형 회귀(릿지)"]=Ridge(alpha=10).fit(X,tr.y).predict(te[RF].fillna(0.5))
    te["그래디언트 부스팅"]=HistGradientBoostingRegressor(max_depth=3,max_iter=200,learning_rate=0.03,min_samples_leaf=200,random_state=0).fit(X,tr.y).predict(te[RF].fillna(0.5))
    preds.append(te)
T=pd.concat(preds); M=["고정 규칙","매년 재선정","선형 회귀(릿지)","그래디언트 부스팅"]; rows=[]
for m in M:
    r=T.groupby("date").apply(lambda g:g.nlargest(20,m).fwd.mean()); ic=T.groupby("date").apply(lambda g:g[m].corr(g.fwd,method="spearman"))
    to=[]; prev=set()
    for t,g in T.groupby("date"):
        cur=set(g.nlargest(20,m).code); to.append(1-len(cur&prev)/20 if prev else 1); prev=cur
    rn=r-np.array(to)*0.003; eq=(1+rn).cumprod()
    rows.append({"방식":m,"연복리%":(eq.iloc[-1]**(12/len(rn))-1)*100,"최대낙폭%":(eq/eq.cummax()-1).min()*100,"샤프":rn.mean()/rn.std()*np.sqrt(12),"순위상관":ic.mean(),"월 교체율%":np.mean(to[1:])*100,**{str(y):((1+rn[str(y)]).prod()-1)*100 for y in range(2022,2027)}})
ew=T.groupby("date").fwd.mean(); eq=(1+ew).cumprod(); rows.append({"방식":"기준: 전 종목 동일 비중","연복리%":(eq.iloc[-1]**(12/len(ew))-1)*100,"최대낙폭%":(eq/eq.cummax()-1).min()*100,"샤프":ew.mean()/ew.std()*np.sqrt(12),**{str(y):((1+ew[str(y)]).prod()-1)*100 for y in range(2022,2027)}})
pd.set_option("display.width",250); pd.set_option("display.float_format",lambda v:f"{v:,.2f}")
R=pd.DataFrame(rows); R.to_csv("sim/flex_results.csv",index=False); print("검증 구간:",T.date.min().date(),"~",T.date.max().date(),"(%d개월, 상위 20종목, 비용 0.3%% 반영)"%T.date.nunique()); print(R.to_string(index=False))
print("\n매년 재선정 시 채택된 지표:")
for y,s in chosen.items(): print(" ",y,":",", ".join(("+" if v>0 else "-")+k for k,v in s.items()))
print("\n[보조] 상위 10%(약 140종목) 묶음의 월평균 초과수익%, 하위 10% 묶음의 월평균 초과수익% (비용 전, 전 종목 평균 대비)")
for m in M:
    top=T.groupby("date").apply(lambda g:g[g[m]>=g[m].quantile(.9)].fwd.mean()-g.fwd.mean()); bot=T.groupby("date").apply(lambda g:g[g[m]<=g[m].quantile(.1)].fwd.mean()-g.fwd.mean())
    print("  %-12s 상위 %+.2f%% (t=%.1f) | 하위 %+.2f%% (t=%.1f)"%(m,top.mean()*100,top.mean()/top.std()*np.sqrt(len(top)),bot.mean()*100,bot.mean()/bot.std()*np.sqrt(len(bot))))
