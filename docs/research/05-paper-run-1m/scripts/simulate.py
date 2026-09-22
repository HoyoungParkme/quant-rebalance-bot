"""100만원 모의 운용. 월말 종가까지의 정보로 판단하고 다음 거래일 시가에 체결한다."""
import pandas as pd, numpy as np, pickle, json, sys, warnings; warnings.filterwarnings("ignore")
RULES=json.load(open("sim/rules.json")); W=RULES["factors"]; USE_TREND=RULES["trend"] if len(sys.argv)<2 else (sys.argv[1]=="trend")
P=pd.read_pickle("sim/panel3.pkl"); O,C,V=pickle.load(open("sim/ocv.pkl","rb")); mac=pickle.load(open("bt/macro.pkl","rb")); KS=mac["KOSPI"]; KQ=mac["KOSDAQ"]
L=pd.read_csv("bt/listing.csv",dtype={"Code":str}).set_index("Code").Name; DLn=pd.read_csv("bt/delisted.csv",dtype={"code":str}).set_index("code").name; NAME={**DLn.to_dict(),**L.to_dict()}
START,END,CASH0,N=pd.Timestamp("2025-01-02"),pd.Timestamp("2026-09-01"),(int(sys.argv[2]) if len(sys.argv)>2 else 1_000_000),10
FEE,SLIP=0.00015,0.002; TAX=lambda d:0.0015 if d.year<=2025 else 0.0020
cal=C.index; days=cal[(cal>=START)&(cal<=END)]; Km=KS.resample("M").last()
mes=sorted(P.date.unique()); decisions={}
for t in mes:
    t=pd.Timestamp(t)
    if t<pd.Timestamp("2024-12-01"): continue
    nxt=cal[cal>t]; 
    if len(nxt)==0: continue
    hist=Km[:t+pd.offsets.MonthEnd(0)]; up=bool(hist.iloc[-1]>hist.iloc[-10:].mean())          # t 시점까지의 월말 종가만 사용
    g=P[P.date==t].dropna(subset=list(W)); s=sum(sg*g[f].rank(pct=True) for f,sg in W.items()); rank=g.assign(score=s).sort_values("score",ascending=False)
    decisions[nxt[0]]={"asof":t,"up":up,"rank":rank[["code","close","score"]].head(60)}
cash=CASH0; hold={}; ledger=[]; eq=[]; cost_total=0; log=[]
for d in days:
    if d in decisions:
        dec=decisions[d]; equity_open=cash+sum(sh*(O.at[d,c] if O.at[d,c]>0 else C.at[d,c]) for c,(sh,_,_) in hold.items())
        target=[]
        if (not USE_TREND) or dec["up"]:
            for r in dec["rank"].itertuples():
                if len(target)>=N: break
                if r.close<=equity_open/N: target.append(r.code)                           # 1주 가격이 1칸 예산 이하인 종목만
        for c in list(hold):                                                                 # 매도 먼저
            if c not in target and V.at[d,c]>0 and O.at[d,c]>0:
                sh,bp,bd=hold.pop(c); px_=O.at[d,c]*(1-SLIP); gross=sh*px_; fee=gross*(FEE+TAX(d)); cash+=gross-fee; cost_total+=fee+sh*O.at[d,c]*SLIP
                ledger.append({"종목":NAME.get(c,c),"코드":c,"매수일":bd.date(),"매도일":d.date(),"주수":sh,"매수가":round(bp),"매도가":round(px_),"손익":round(gross-fee-sh*bp),"수익률%":round((gross-fee)/(sh*bp)*100-100,1)})
        for c in target:                                                                     # 빈 칸만 매수
            if c in hold or len(hold)>=N: continue
            if not (V.at[d,c]>0 and O.at[d,c]>0): continue
            px_=O.at[d,c]*(1+SLIP); budget=min(equity_open/N,cash); sh=int(budget//(px_*(1+FEE)))
            if sh>=1: cash-=sh*px_*(1+FEE); cost_total+=sh*px_*FEE+sh*O.at[d,c]*SLIP; hold[c]=(sh,px_*(1+FEE),d)
        log.append({"체결일":d.date(),"판단일":dec["asof"].date(),"코스피추세":"위" if dec["up"] else "아래","보유수":len(hold),"현금":round(cash)})
    for c in [c for c in hold if d in decisions and False]: pass
    eq.append({"date":d,"equity":cash+sum(sh*C.at[d,c] for c,(sh,_,_) in hold.items()),"n":len(hold),"cash":cash})
E=pd.DataFrame(eq).set_index("date"); last=days[-1]
open_pos=[{"종목":NAME.get(c,c),"코드":c,"매수일":bd.date(),"주수":sh,"매수가":round(bp),"현재가":round(C.at[last,c]),"평가손익":round(sh*(C.at[last,c]-bp)),"수익률%":round(C.at[last,c]/bp*100-100,1)} for c,(sh,bp,bd) in hold.items()]
Lg=pd.DataFrame(ledger); OP=pd.DataFrame(open_pos); tag=("trend" if USE_TREND else "notrend")+(f"_{CASH0}" if len(sys.argv)>2 else "")
E.to_csv(f"sim/equity_{tag}.csv"); Lg.to_csv(f"sim/ledger_{tag}.csv",index=False); OP.to_csv(f"sim/open_{tag}.csv",index=False); pd.DataFrame(log).to_csv(f"sim/rebalance_{tag}.csv",index=False)
fin=E.equity.iloc[-1]; mdd=(E.equity/E.equity.cummax()-1).min(); ks=KS.reindex(days).ffill(); kq=KQ.reindex(days).ffill()
print("=== 추세 필터 %s ==="%("적용" if USE_TREND else "미적용"))
print("기간 %s ~ %s | 시작 1,000,000원 → 최종 %s원 | 손익 %+d원 (%+.1f%%) | 최대 손실폭 %.1f%% | 낸 비용 합계 %s원"%(days[0].date(),last.date(),f"{fin:,.0f}",fin-CASH0,(fin/CASH0-1)*100,mdd*100,f"{cost_total:,.0f}"))
print("같은 기간 코스피 %+.1f%% (100만원 → %s원, 최대 손실폭 %.1f%%) | 코스닥 %+.1f%%"%((ks.iloc[-1]/KS[:days[0]].iloc[-2]-1)*100,f"{CASH0*ks.iloc[-1]/KS[:days[0]].iloc[-2]:,.0f}",(ks/ks.cummax()-1).min()*100,(kq.iloc[-1]/KQ[:days[0]].iloc[-2]-1)*100))
M=E.equity.resample("M").last(); Mr=M.pct_change(); Mr.iloc[0]=M.iloc[0]/CASH0-1; kM=ks.resample("M").last(); kr=kM.pct_change(); kr.iloc[0]=kM.iloc[0]/KS[:days[0]].iloc[-2]-1
T=pd.DataFrame({"월말 평가액":M.round().astype(int),"월 수익%":(Mr*100).round(1),"코스피%":(kr*100).round(1),"보유 종목":E.n.resample("M").last()}); T.index=T.index.strftime("%Y-%m"); print(T.to_string())
if len(Lg): print("\n매매 완료 %d건: 이익 %d건 / 손실 %d건 | 실현손익 합계 %+d원 | 평균 %+.1f%%, 최고 %+.1f%%, 최저 %+.1f%%"%(len(Lg),(Lg.손익>0).sum(),(Lg.손익<=0).sum(),Lg.손익.sum(),Lg["수익률%"].mean(),Lg["수익률%"].max(),Lg["수익률%"].min()))
if len(OP): print("보유 중 %d종목 평가손익 합계 %+d원 | 현금 %s원"%(len(OP),OP.평가손익.sum(),f"{cash:,.0f}"))
