import pandas as pd, numpy as np, FinanceDataReader as fdr, warnings; warnings.filterwarnings("ignore")
etf=fdr.DataReader("069500","2024-12-20","2026-09-01"); TOTAL=1_000_000; FEE=0.00015
def load(tag,cash): 
    f=f"sim/equity_{tag}.csv" if cash==TOTAL else f"sim/equity_{tag}_{cash}.csv"; return pd.read_csv(f,parse_dates=["date"]).set_index("date").equity
days=load("trend",TOTAL).index; o0=etf.Open.loc[days[0]]; ec=etf.Close.reindex(days).ffill()
print("KODEX 200: 2025-01-02 시가 %s원 → 2026-09-01 종가 %s원 (%+.1f%%)"%(f"{o0:,.0f}",f"{ec.iloc[-1]:,.0f}",(ec.iloc[-1]/o0-1)*100))
rows=[]
for tag,lab in [("trend","추세 필터 적용"),("notrend","추세 필터 미적용")]:
    for w in [0,20,40,50,60,80,100]:
        sc=int(TOTAL*w/100); ecash=TOTAL-sc
        sh=int(ecash//(o0*(1+FEE))); left=ecash-sh*o0*(1+FEE); E_etf=sh*ec+left
        E_s=load(tag,sc) if sc>0 else pd.Series(0.0,index=days)
        E=E_etf+E_s; m=E.resample("M").last().pct_change().dropna(); dd=E/E.cummax()-1; r=E.pct_change().dropna()
        rows.append({"구분":lab,"전략 비중":f"{w}%","지수 비중":f"{100-w}%","ETF 주수":sh,"최종 평가액":round(E.iloc[-1]),"수익률%":round((E.iloc[-1]/TOTAL-1)*100,1),"최대 손실폭%":round(dd.min()*100,1),"최악의 달%":round(m.min()*100,1),"일 변동성(연환산)%":round(r.std()*np.sqrt(250)*100,1),"수익/손실폭":round((E.iloc[-1]/TOTAL-1)/abs(dd.min()),2)})
T=pd.DataFrame(rows); T.to_csv("sim/blend_results.csv",index=False); pd.set_option("display.width",250); print(T.to_string(index=False))
a=load("notrend",TOTAL).pct_change().dropna(); b=ec.pct_change().dropna(); print("\n전략(필터 미적용)과 KODEX 200의 일 수익률 상관: %.2f"%a.corr(b))
ma=load("notrend",TOTAL).resample("M").last().pct_change().dropna(); mb=ec.resample("M").last().pct_change().dropna(); print("월 수익률 상관: %.2f | 지수가 하락한 달(%d개월) 전략 평균 %+.1f%%, 지수 평균 %+.1f%%"%(ma.corr(mb),(mb<0).sum(),ma[mb<0].mean()*100,mb[mb<0].mean()*100))
