import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
P1=pd.read_pickle("bt/panel.pkl"); P2=pd.read_pickle("bt/panel2.pkl"); mac=pickle.load(open("bt/macro.pkl","rb")); K=mac["KOSPI"].resample("M").last(); trend=(K>K.rolling(10).mean())
DL=pd.read_csv("bt/delisted.csv",dtype={"code":str}).set_index("code")
pd.set_option("display.width",250); pd.set_option("display.float_format",lambda v:f"{v:,.2f}")
def spread(P,f):
    ls=[]
    for t,g in P.dropna(subset=[f]).groupby("date"):
        if len(g)<100: continue
        b=pd.qcut(g[f].rank(method="first"),5,labels=False); r=g.groupby(b).fwd.mean(); ls.append(r[4]-r[0])
    ls=pd.Series(ls); return ls.mean()*100, ls.mean()/ls.std()*np.sqrt(len(ls))
print("[1] 지표별 상위20%-하위20% 월 수익차(%) / t : 생존 종목만 → 폐지 포함")
for f in ["OPG","ROE","EP","SP","BP","VOL60","TURN20","HIGH52","MOM12_1","MOM3","VOLSURGE"]:
    a=spread(P1,f); b=spread(P2,f); print("  %-9s %6.2f / %5.1f   →  %6.2f / %5.1f"%(f,a[0],a[1],b[0],b[1]))
SC={"S1 저평가":{"EP":1,"BP":1},"S2 저평가+우량":{"EP":1,"BP":1,"ROE":1,"OPG":1},"S3 모멘텀":{"MOM12_1":1,"HIGH52":1},"S5 거래량급증 추격":{"VOLSURGE":1,"MOM3":1},"S6 종합":{"EP":1,"BP":1,"ROE":1,"OPG":1,"VOL60":-1,"TURN20":-1}}
def run(P,w,N=20,cost=0.003,filt=False,keep=False):
    prev=set(); rets={}; picks=[]
    for t,g in P.groupby("date"):
        g=g.dropna(subset=list(w))
        if len(g)<100: continue
        s=sum(sg*g[f].rank(pct=True) for f,sg in w.items()); sel=g.loc[s.nlargest(N).index]; pick=set(sel.code)
        r=sel.fwd.mean(); to=1-len(pick&prev)/N if prev else 1
        if filt and not bool(trend.get(t+pd.offsets.MonthEnd(0),True)): r=0.0; pick=set(); to=1 if prev else 0
        elif keep: picks.append(sel[["date","code","fwd"]+(["delisted","del_type"] if "delisted" in sel else [])])
        rets[t]=r-to*cost; prev=pick
    return pd.Series(rets),(pd.concat(picks) if picks else None)
def st(r):
    eq=(1+r).cumprod(); return (eq.iloc[-1]**(12/len(r))-1)*100,(eq/eq.cummax()-1).min()*100,r.mean()/r.std()*np.sqrt(12)
print("\n[2] 시나리오: 연복리% / 최대낙폭% / 샤프 : 생존 종목만 → 폐지 포함")
rows=[]
for n,w in SC.items():
    a=st(run(P1,w)[0]); b=st(run(P2,w)[0]); print("  %-16s %6.1f / %6.1f / %4.2f  →  %6.1f / %6.1f / %4.2f"%((n,)+a+b)); rows.append((n,)+a+b)
a=st(run(P1,SC["S6 종합"],filt=True)[0]); b=st(run(P2,SC["S6 종합"],filt=True)[0]); print("  %-16s %6.1f / %6.1f / %4.2f  →  %6.1f / %6.1f / %4.2f"%(("S7 종합+추세필터",)+a+b)); rows.append(("S7 종합+추세필터",)+a+b)
a=st(P1.groupby("date").fwd.mean()); b=st(P2.groupby("date").fwd.mean()); print("  %-16s %6.1f / %6.1f / %4.2f  →  %6.1f / %6.1f / %4.2f"%(("기준: 전종목 동일가중",)+a+b)); rows.append(("기준: 전종목 동일가중",)+a+b)
pd.DataFrame(rows,columns=["시나리오","연복리_생존만","낙폭_생존만","샤프_생존만","연복리_폐지포함","낙폭_폐지포함","샤프_폐지포함"]).to_csv("bt/reverify_scenarios.csv",index=False)
print("\n[3] 폐지 포함 기준 S6 종합: 구간별·연도별")
r,pk=run(P2,SC["S6 종합"],keep=True); r7,_=run(P2,SC["S6 종합"],filt=True)
for lab,x in [("S6",r),("S7",r7)]:
    print("  %s 2020-23 연복리 %.1f%% | 2024-26 연복리 %.1f%% | 연도별:"%(lab,st(x[:"2023-12"])[0],st(x["2024-01":])[0]),{y:round(((1+x[str(y)]).prod()-1)*100,1) for y in range(2020,2027)})
print("\n[4] 견고성 (폐지 포함): 종목수·비용·범위")
for lab,P in [("기본",P2),("중대형(시총3000억+,거래대금20억+)",P2[(P2.mcap>=3e11)&(P2.tv20>=2e9)])]:
    for N,c in [(10,.003),(20,.003),(30,.003),(50,.003),(20,.01)]:
        s=st(run(P,SC["S6 종합"],N,c)[0]); print("  %-30s N=%2d 비용%.1f%% : 연 %5.1f%% / 낙폭 %6.1f%% / 샤프 %.2f"%(lab,N,c*100,s[0],s[1],s[2]))
print("\n[5] S6이 실제로 고른 종목 중 이후 상장폐지된 종목")
d=pk[pk.delisted]; print("  전체 선택 %d건 중 폐지 종목 %d건 (종목 %d개)"%(len(pk),len(d),d.code.nunique()))
if len(d): 
    d=d.assign(name=DL.name.reindex(d.code).values,reason=DL.reason.reindex(d.code).str[:24].values)
    print(d.groupby(["code","name","del_type","reason"]).agg(보유개월=("fwd","size"),누적수익=("fwd",lambda x:((1+x).prod()-1)*100),최악의달=("fwd",lambda x:x.min()*100)).reset_index().to_string(index=False))
print("\n[6] 부실 폐지 종목 86개는 폐지 전 12개월 동안 종합점수 몇 분위에 있었나")
g=P2.dropna(subset=list(SC["S6 종합"])).copy(); g["score"]=sum(s*g.groupby("date")[f].rank(pct=True) for f,s in SC["S6 종합"].items()); g["q"]=g.groupby("date").score.transform(lambda x:pd.qcut(x.rank(method="first"),5,labels=False))+1
b=g[g.del_type=="부실·규정위반"]; print("  관측 %d건 분위 분포(1=최하위,5=최상위):"%len(b),(b.q.value_counts(normalize=True).sort_index()*100).round(0).to_dict())
allb=P2[P2.del_type=="부실·규정위반"]; print("  참고: 재무 지표가 없어 점수를 못 매긴 관측 %d건"%(len(allb)-len(b)))
