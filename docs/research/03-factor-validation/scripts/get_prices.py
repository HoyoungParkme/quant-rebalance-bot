import pandas as pd, numpy as np, FinanceDataReader as fdr, pickle, time, warnings; warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor
L=fdr.StockListing("KRX"); print(L.shape, list(L.columns))
L=L[L.Market.isin(["KOSPI","KOSDAQ","KOSDAQ GLOBAL"])]
L=L[L.Code.str.match(r"^\d{5}0$")]                       # 보통주만
L=L[~L.Name.str.contains("스팩|리츠|인프라")]
L.to_csv("bt/listing.csv",index=False); codes=list(L.Code); print("universe",len(codes))
def get(c):
    for _ in range(3):
        try: return c,fdr.DataReader(c,"2019-01-01","2026-09-18")[["Open","High","Low","Close","Volume"]]
        except Exception: time.sleep(1)
    return c,None
t=time.time(); px={}
with ThreadPoolExecutor(8) as ex:
    for c,x in ex.map(get,codes):
        if x is not None and len(x)>20: px[c]=x
print("prices",len(px),"%.0fs"%(time.time()-t)); pickle.dump(px,open("bt/px_all.pkl","wb"))
mac={}
for k,s in {"KOSPI":"KS11","KOSDAQ":"KQ11","USDKRW":"USD/KRW","SP500":"US500","VIX":"FRED:VIXCLS","US10Y":"FRED:DGS10","WTI":"FRED:DCOILWTICO","DXY":"FRED:DTWEXBGS"}.items():
    try:
        d=fdr.DataReader(s,"2019-01-01","2026-09-18"); mac[k]=d["Close"] if "Close" in d else d.iloc[:,0]; print(k,len(mac[k]),mac[k].index[-1].date())
    except Exception as e: print("ERR",k,str(e)[:100])
pickle.dump(mac,open("bt/macro.pkl","wb"))
# 유동성 상위 유니버스 (월말 기준 20일 평균 거래대금 상위 500에 한 번이라도 든 종목)
C=pd.DataFrame({c:p.Close for c,p in px.items()}); V=pd.DataFrame({c:p.Volume for c,p in px.items()})
tv=(C*V).rolling(20).mean(); me=tv.resample("M").last().loc["2019-12":"2026-08"]
u=set()
for d,row in me.iterrows(): u|=set(row.nlargest(500).index)
print("flow universe",len(u)); open("bt/flow_universe.txt","w").write("\n".join(sorted(u)))
