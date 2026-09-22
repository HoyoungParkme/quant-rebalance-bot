"""2024-12까지의 데이터만으로 규칙을 고른다. 기준은 사전에 고정: 월 수익차의 |t| >= 2.0인 지표만, 동일 가중."""
import pandas as pd, numpy as np, pickle, json, warnings; warnings.filterwarnings("ignore")
P=pd.read_pickle("sim/panel3.pkl"); IS=P[P.date<="2024-11-30"]          # 2024-11 말 매수분의 수익이 2024-12 말에 확정
print("규칙 선정에 쓴 기간:",IS.date.min().date(),"~",IS.date.max().date(),"(%d개월)"%IS.date.nunique())
cands=["EP","BP","SP","ROE","OPG","OPG_Q","MOM12_1","MOM3","HIGH52","VOL60","TURN20","VOLSURGE"]; sel={}
for f in cands:
    ls=[]
    for t,g in IS.dropna(subset=[f]).groupby("date"):
        if len(g)<100: continue
        b=pd.qcut(g[f].rank(method="first"),5,labels=False); r=g.groupby(b).fwd.mean(); ls.append(r[4]-r[0])
    ls=pd.Series(ls); m=ls.mean()*100; tt=ls.mean()/ls.std()*np.sqrt(len(ls)); ok=abs(tt)>=2.0
    print("  %-9s 월 %6.2f%%  t=%5.1f  %s"%(f,m,tt,"채택 (%s)"%("높을수록 매수" if tt>0 else "낮을수록 매수") if ok else "기각"))
    if ok: sel[f]=1 if tt>0 else -1
mac=pickle.load(open("bt/macro.pkl","rb")); K=mac["KOSPI"].resample("M").last(); up=(K>K.rolling(10).mean())
ew=IS.groupby("date").fwd.mean(); u=pd.Series([bool(up.get(t+pd.offsets.MonthEnd(0),True)) for t in ew.index],index=ew.index)
print("\n추세 필터(2024-12까지): 코스피가 10개월 평균 위일 때 다음 달 평균 %.2f%% (%d개월) / 아래일 때 %.2f%% (%d개월)"%(ew[u].mean()*100,u.sum(),ew[~u].mean()*100,(~u).sum()))
use_trend=bool(ew[u].mean()>ew[~u].mean()); print("→ 추세 필터",("채택" if use_trend else "기각"))
json.dump({"factors":sel,"trend":use_trend},open("sim/rules.json","w")); print("\n고정된 규칙:",sel,"| 추세필터:",use_trend)
