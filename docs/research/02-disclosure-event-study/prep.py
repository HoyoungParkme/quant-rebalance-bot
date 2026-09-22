import pandas as pd, FinanceDataReader as fdr, pickle, time
from concurrent.futures import ThreadPoolExecutor
a=pd.read_csv("events_raw.csv",dtype=str); b=pd.read_csv("events_raw2.csv",dtype=str)
d=pd.concat([a,b]); d["type"]=d.type.str.replace(r"주요사항보고서\((.*)\)",r"\1",regex=True)
n0=len(d)
d=d[d.market.isin(["유가증권시장","코스닥시장"])]
d=d[~d.title.str.startswith("[")]            # 정정·첨부 제외: 최초 공시만
d=d[~d.title.str.contains("자회사")]
d["date"]=pd.to_datetime(d.date,format="%Y.%m.%d")
d=d.drop_duplicates(["type","corp","date"])
print("raw",n0,"-> clean",len(d)); print(d.type.value_counts().to_string())
try:
    L=fdr.StockListing("KRX")[["Code","Name","Market"]]
except Exception as e:
    print("KRX listing failed",e); L=fdr.StockListing("KRX-DESC")[["Code","Name","Market"]]
d=d.merge(L.rename(columns={"Name":"corp"})[["corp","Code"]],on="corp",how="left")
print("ticker matched: %.1f%%"%(d.Code.notna().mean()*100)); d=d.dropna(subset=["Code"])
d.to_csv("events.csv",index=False)
codes=sorted(d.Code.unique()); print("tickers",len(codes))
def get(c):
    for _ in range(3):
        try:
            x=fdr.DataReader(c,"2023-12-01","2026-09-18")[["Open","Close","Volume"]]; return c,x
        except Exception: time.sleep(1)
    return c,None
t=time.time(); px={}
with ThreadPoolExecutor(8) as ex:
    for c,x in ex.map(get,codes):
        if x is not None and len(x)>50: px[c]=x
print("prices",len(px),"in %.0fs"%(time.time()-t))
idx={"유가증권시장":fdr.DataReader("KS11","2023-12-01","2026-09-18")[["Open","Close"]],"코스닥시장":fdr.DataReader("KQ11","2023-12-01","2026-09-18")[["Open","Close"]]}
print({k:(len(v),str(v.index[-1].date())) for k,v in idx.items()})
pickle.dump((px,idx),open("px.pkl","wb"))
