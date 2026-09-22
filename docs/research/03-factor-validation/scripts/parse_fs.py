import zipfile, io, pandas as pd, glob, re, warnings; warnings.filterwarnings("ignore")
ITEMS={"Equity":"equity","EquityAttributableToOwnersOfParent":"equity_own","Assets":"assets","Liabilities":"liab",
       "ProfitLoss":"ni","ProfitLossAttributableToOwnersOfParent":"ni_own","Revenue":"rev","OperatingIncomeLoss":"op"}
out=[]
for zf in sorted(glob.glob("dartfs/20*_4Q_*.zip")):
    z=zipfile.ZipFile(zf)
    for n in z.namelist():
        try: nm=n.encode("cp437").decode("cp949")
        except Exception: nm=n
        cons="연결" in nm
        df=pd.read_csv(io.BytesIO(z.read(n)),sep="\t",encoding="cp949",dtype=str,on_bad_lines="skip",quoting=3)
        df.columns=[c.strip() for c in df.columns]
        if zf.endswith("zip") and "2019_4Q_PL" in zf and not cons: print(nm, list(df.columns))
        key=df["항목코드"].str.replace(r"^(ifrs-full_|ifrs_|dart_)","",regex=True)
        df=df[key.isin(ITEMS)].copy(); df["item"]=key[df.index].map(ITEMS)
        val=[c for c in df.columns if c.startswith("당기")]
        vcol="당기" if "당기" in df.columns else val[-1]
        df["v"]=pd.to_numeric(df[vcol].str.replace(",",""),errors="coerce")
        df["code"]=df["종목코드"].str.extract(r"(\d{6})"); df["cons"]=cons; df["fy_end"]=df["결산기준일"]
        out.append(df[["code","회사명","시장구분","업종명","fy_end","cons","item","v"]].dropna(subset=["v","code"]))
A=pd.concat(out); A=A.drop_duplicates(["code","fy_end","cons","item"])
W=A.pivot_table(index=["code","fy_end","cons"],columns="item",values="v",aggfunc="first").reset_index()
W=W.sort_values("cons",ascending=False).drop_duplicates(["code","fy_end"])      # 연결 우선
W["equity_use"]=W.equity_own.fillna(W.equity); W["ni_use"]=W.ni_own.fillna(W.ni)
W["fy_end"]=pd.to_datetime(W.fy_end); W["avail"]=W.fy_end+pd.Timedelta(days=95)   # 결산 후 약 3개월 뒤 공개 가정
W.to_csv("bt/fs.csv",index=False)
print(W.shape); print(W.groupby(W.fy_end.dt.year).agg(n=("code","count"),eq=("equity_use","count"),ni=("ni_use","count"),rev=("rev","count"),op=("op","count")).to_string())
print(W[W.code=="005930"][["fy_end","cons","equity_use","ni_use","rev","op"]].to_string())
