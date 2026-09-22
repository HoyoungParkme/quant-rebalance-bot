import pandas as pd, numpy as np, pickle, warnings; warnings.filterwarnings("ignore")
px,idx=pickle.load(open("px.pkl","rb")); ev=pd.read_csv("events.csv",dtype={"Code":str},parse_dates=["date"])
cal=idx["유가증권시장"].index
O=pd.DataFrame({c:p.Open.where(p.Open>0) for c,p in px.items()}).reindex(cal)
C=pd.DataFrame({c:p.Close.where((p.Close>0)&(p.Open>0)) for c,p in px.items()}).reindex(cal)
mk=ev.drop_duplicates("Code").set_index("Code").market
H=[1,3,5,10,20]
W={"pre5":C.shift(1)/C.shift(6)-1,"day0":C/C.shift(1)-1,"react2":C.shift(-1)/C.shift(1)-1,"gap":O.shift(-1)/C-1}
for h in H: W[f"hold{h}"]=C.shift(-h)/O.shift(-1)-1          # 모두 공시일(t0) 행 기준
AB={}
for k,w in W.items():
    a=w.copy()
    for m in ["유가증권시장","코스닥시장"]:
        cols=[c for c in w.columns if mk.get(c)==m]
        a[cols]=w[cols].sub(w[cols].mean(axis=1),axis=0)     # 같은 시장 동일가중 평균 차감
    AB[k]=a
rows=[]
for e in ev.itertuples():
    if e.Code not in C.columns: continue
    i=cal.searchsorted(e.date)
    if i>=len(cal) or (cal[i]-e.date).days>4: continue
    r={"type":e.type,"market":e.market,"code":e.Code,"date":e.date,"year":e.date.year}
    for k in AB: r[k]=AB[k].iloc[i][e.Code]
    rows.append(r)
R=pd.DataFrame(rows).dropna(); R.to_csv("event_returns2.csv",index=False)
print("events used:",len(R),"|",R.date.min().date(),"~",R.date.max().date())
def summ(g,col):
    x=g[col]; x=x.clip(x.quantile(.005),x.quantile(.995))
    return pd.Series({"N":len(x),"평균%":x.mean()*100,"중앙%":x.median()*100,"상승비율%":(x>0).mean()*100,"t":x.mean()/x.std()*np.sqrt(len(x))})
pd.set_option("display.width",220); pd.set_option("display.float_format",lambda v:f"{v:,.2f}")
order=["단일판매ㆍ공급계약체결","자기주식취득결정","주식소각결정","무상증자결정","유상증자결정","전환사채권발행결정"]
for col,name in [("pre5","[A] 공시 전 5일"),("react2","[B] 공시 반응: 전일 종가→다음날 종가"),("hold1","[C1] 다음날 시가 매수→당일 종가"),("hold5","[C2] 다음날 시가 매수→5일"),("hold20","[C3] 다음날 시가 매수→20일")]:
    print("\n"+name+" (동일가중 시장평균 차감, 비용 전)"); print(R.groupby("type").apply(lambda g:summ(g,col)).loc[order].to_string())
print("\n[D] 보유기간별 평균 초과수익% (비용 0.25% 차감 후)")
print((R.groupby("type")[[f"hold{h}" for h in H]].mean().loc[order]*100-0.25).to_string())
print("\n[D2] 같은 표, 중앙값 기준")
print((R.groupby("type")[[f"hold{h}" for h in H]].median().loc[order]*100-0.25).to_string())
print("\n[E] 연도별 안정성: hold5 평균% / 상승비율% / N")
for t in ["자기주식취득결정","무상증자결정","단일판매ㆍ공급계약체결"]:
    g=R[R.type==t].groupby("year").hold5.agg(lambda x:x.mean()*100).round(2).astype(str)+" / "+R[R.type==t].groupby("year").hold5.agg(lambda x:(x>0).mean()*100).round(0).astype(str)+" / "+R[R.type==t].groupby("year").hold5.count().astype(str)
    print(t,dict(g))
print("\n[F] 공급계약: 공시 당일 반응 크기별 이후 수익 (겹침 없음: 당일 종가까지로 분류, 다음날 시가부터 측정)")
s=R[R.type=="단일판매ㆍ공급계약체결"].copy(); s["b"]=pd.cut(s.day0,[-1,-.03,0,.03,.1,5],labels=["-3% 미만","-3~0%","0~3%","3~10%","10% 초과"])
print(s.groupby("b").apply(lambda g:pd.Series({"N":len(g),"당일%":g.day0.mean()*100,"다음날갭%":g.gap.mean()*100,"hold1%":g.hold1.mean()*100,"hold5평균%":g.hold5.mean()*100,"hold5중앙%":g.hold5.median()*100,"상승비율%":(g.hold5>0).mean()*100})).to_string())
print("\n[G] 자사주취득: 시장별 / 당일 반응별")
b=R[R.type=="자기주식취득결정"].copy()
print(b.groupby("market").apply(lambda g:pd.Series({"N":len(g),"react2%":g.react2.mean()*100,"gap%":g.gap.mean()*100,"hold1%":g.hold1.mean()*100,"hold5%":g.hold5.mean()*100,"hold5상승비율":(g.hold5>0).mean()*100})).to_string())
b["b"]=pd.cut(b.day0,[-1,0,.03,5],labels=["당일 하락","0~3%","3% 초과"])
print(b.groupby("b").apply(lambda g:pd.Series({"N":len(g),"gap%":g.gap.mean()*100,"hold5%":g.hold5.mean()*100,"hold5상승비율":(g.hold5>0).mean()*100})).to_string())
