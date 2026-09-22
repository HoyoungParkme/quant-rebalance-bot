import pandas as pd, numpy as np, pickle, FinanceDataReader as fdr, warnings; warnings.filterwarnings("ignore")
mac=pickle.load(open("bt/macro.pkl","rb")); P=pd.read_pickle("bt/panel.pkl")
pd.set_option("display.width",220); pd.set_option("display.float_format",lambda v:f"{v:,.2f}")
M=pd.DataFrame({k:v.resample("M").last() for k,v in mac.items()}).loc["2019-06":"2026-08"]
X=pd.DataFrame({"KOSPI수익률":M.KOSPI.pct_change(),"KOSDAQ수익률":M.KOSDAQ.pct_change(),"원달러 변화":M.USDKRW.pct_change(),"S&P500 수익률":M.SP500.pct_change(),
 "VIX 변화":M.VIX.diff(),"VIX 수준":M.VIX,"미10년금리 변화":M.US10Y.diff(),"유가 변화":M.WTI.pct_change(),"달러지수 변화":M.DXY.pct_change()}).loc["2020-01":]
ew=P.groupby("date").fwd.mean(); ew.index=ew.index+pd.offsets.MonthEnd(0); ew=ew.shift(1,freq="M"); X["중소형 동일가중"]=ew
out=[]
for c in X.columns[2:-1]:
    for tgt in ["KOSPI수익률","중소형 동일가중"]:
        a=X[[c,tgt]].dropna(); same=a[c].corr(a[tgt]); b=pd.concat([X[c],X[tgt].shift(-1)],axis=1).dropna(); nxt=b.iloc[:,0].corr(b.iloc[:,1])
        out.append({"외부 요인":c,"대상":tgt,"같은 달 상관":same,"다음 달 상관":nxt,"N":len(b)})
print("[1] 외부 요인과 월 수익률의 상관계수 (N≈79, |0.22| 이상이면 5% 유의)"); print(pd.DataFrame(out).pivot(index="외부 요인",columns="대상",values=["같은 달 상관","다음 달 상관"]).to_string())
print("\n[2] 월말 조건별 다음 달 수익률(%)")
ma10=M.KOSPI.rolling(10).mean(); cond={"KOSPI가 10개월 평균 위":M.KOSPI>ma10,"VIX 25 초과":M.VIX>25,"원달러 3개월 상승(원화약세)":M.USDKRW.pct_change(3)>0,"S&P500 월 -5% 이하":M.SP500.pct_change()<-0.05,"미10년금리 3개월 상승":M.US10Y.diff(3)>0}
rows=[]
for n,cnd in cond.items():
    for tgt in ["KOSPI수익률","중소형 동일가중"]:
        nx=X[tgt].shift(-1); c=cnd.reindex(nx.index).fillna(False)
        rows.append({"조건":n,"대상":tgt,"충족 개월":int(c[nx.notna()].sum()),"충족 시 평균":nx[c].mean()*100,"미충족 시 평균":nx[~c].mean()*100,"충족 시 상승비율%":(nx[c]>0).mean()*100})
print(pd.DataFrame(rows).to_string(index=False))
print("\n[3] 일 단위: 전날 밤 미국장이 한국장에 반영되는 위치")
k=fdr.DataReader("KS11","2020-01-01","2026-09-18"); sp=mac["SP500"].pct_change()
d=pd.DataFrame({"gap":k.Open/k.Close.shift(1)-1,"intraday":k.Close/k.Open-1}); d["sp_prev"]=sp.reindex(d.index,method="ffill").shift(1)
spd=sp.copy(); spd.index=spd.index+pd.Timedelta(days=1); d["sp_prev"]=[sp[:t-pd.Timedelta(days=1)].iloc[-1] if len(sp[:t-pd.Timedelta(days=1)]) else np.nan for t in d.index]
d=d.dropna(); print("미국 전일 수익률 vs 한국 시가 갭 상관: %.2f | vs 한국 장중(시가→종가) 상관: %.2f | N=%d"%(d.sp_prev.corr(d.gap),d.sp_prev.corr(d.intraday),len(d)))
for lab,m in [("미국 -1.5% 이하",d.sp_prev<=-0.015),("미국 +1.5% 이상",d.sp_prev>=0.015)]:
    print(f"{lab}: {m.sum()}일, 한국 시가 갭 평균 {d.gap[m].mean()*100:.2f}%, 장중 평균 {d.intraday[m].mean()*100:.2f}%, 장중 상승비율 {(d.intraday[m]>0).mean()*100:.0f}%")
