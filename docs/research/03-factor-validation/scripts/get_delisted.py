import pandas as pd, numpy as np, pickle, time, zipfile, io, glob, FinanceDataReader as fdr, warnings; warnings.filterwarnings("ignore")
from concurrent.futures import ThreadPoolExecutor
px=pickle.load(open("bt/px_all.pkl","rb")); D=pd.read_csv("bt/delisted.csv",dtype={"code":str})
D=D[~D.type.isin(["스팩","펀드·만기"])&D.code.str.endswith("0")]; todo=[c for c in D.code if c not in px]
print("폐지 종목(스팩·펀드 제외):",len(D),"| 이미 시세 있음(이전상장 등):",len(D)-len(todo),"| 새로 받을 것:",len(todo))
def get(c):
    for _ in range(3):
        try: return c,fdr.DataReader(c,"2019-01-01","2026-09-18")[["Open","High","Low","Close","Volume"]]
        except Exception: time.sleep(1)
    return c,None
got={}
with ThreadPoolExecutor(8) as ex:
    for c,x in ex.map(get,todo):
        if x is not None and len(x)>20: got[c]=x
print("시세 확보:",len(got)); pickle.dump(got,open("bt/px_del.pkl","wb"))
ex1=D[D.type=="부실·규정위반"].code; ex1=[c for c in ex1 if c in got][:3]
for c in ex1:
    x=got[c]; tr=x[x.Volume>0]; print(c,D.set_index("code").name[c],"| 폐지일",D.set_index("code").del_date[c],"| 마지막 거래일",tr.index[-1].date(),"| 마지막 10거래일 종가:",list(tr.Close.tail(10).astype(int)))
# 주당순이익으로 주식 수 역산
rows=[]
for zf in sorted(glob.glob("dartfs/20*_4Q_PL_*.zip")):
    z=zipfile.ZipFile(zf)
    for n in z.namelist():
        try: nm=n.encode("cp437").decode("cp949")
        except Exception: nm=n
        df=pd.read_csv(io.BytesIO(z.read(n)),sep="\t",encoding="cp949",dtype=str,on_bad_lines="skip",quoting=3); df.columns=[c.strip() for c in df.columns]
        if "당기" not in df.columns: continue
        k=df["항목코드"].str.replace(r"^(ifrs-full_|ifrs_|dart_)","",regex=True); df=df[k.isin(["BasicEarningsLossPerShare","BasicEarningsLossPerShareFromContinuingOperations"])]
        o=pd.DataFrame({"code":df["종목코드"].str.extract(r"(\d{6})")[0],"fy_end":df["결산기준일"],"cons":"연결" in nm,"eps":pd.to_numeric(df["당기"].str.replace(",",""),errors="coerce")}); rows.append(o)
E=pd.concat(rows).dropna().sort_values("cons",ascending=False).drop_duplicates(["code","fy_end"])
fs=pd.read_csv("bt/fs.csv",dtype={"code":str}); m=fs.merge(E[["code","fy_end","eps"]],on=["code","fy_end"],how="left")
m["sh"]=np.where(m.eps.abs()>=5,(m.ni_use/m.eps).abs(),np.nan)
L=pd.read_csv("bt/listing.csv",dtype={"Code":str}).set_index("Code"); last=m.dropna(subset=["sh"]).sort_values("fy_end").groupby("code").tail(1).set_index("code")
chk=(last.sh/L.Stocks.reindex(last.index)).dropna(); print("\n역산 주식 수 / 실제 상장주식 수 (현재 상장 종목 %d개 검증): 중앙값 %.2f, 0.8~1.25 범위 비율 %.0f%%"%(len(chk),chk.median(),((chk>0.8)&(chk<1.25)).mean()*100))
last[["sh","fy_end"]].to_csv("bt/implied_shares.csv"); print("폐지 종목 중 주식 수 역산 성공:",len(set(last.index)&set(got)),"/",len(got))
