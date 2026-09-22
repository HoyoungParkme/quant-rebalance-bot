import zipfile, io, glob, re, pandas as pd, numpy as np, warnings; warnings.filterwarnings("ignore")
def load(zf):
    out=[]; z=zipfile.ZipFile(zf)
    for n in z.namelist():
        try: nm=n.encode("cp437").decode("cp949")
        except Exception: nm=n
        df=pd.read_csv(io.BytesIO(z.read(n)),sep="\t",encoding="cp949",dtype=str,on_bad_lines="skip",quoting=3); df.columns=[c.strip() for c in df.columns]
        key=df["항목코드"].str.replace(r"^(ifrs-full_|ifrs_|dart_)","",regex=True); df=df[key=="OperatingIncomeLoss"].copy()
        cur3=[c for c in df.columns if c.startswith("당기") and "3개월" in c]; prv3=[c for c in df.columns if c.startswith("전기") and "3개월" in c]
        curc=[c for c in df.columns if c.startswith("당기") and "누적" in c]; prvc=[c for c in df.columns if c.startswith("전기") and "누적" in c]
        f=lambda s: pd.to_numeric(s.str.replace(",",""),errors="coerce")
        o=pd.DataFrame({"code":df["종목코드"].str.extract(r"(\d{6})")[0],"qend":df["결산기준일"],"cons":"연결" in nm})
        if cur3: o["q"]=f(df[cur3[0]]); o["q_prev"]=f(df[prv3[0]]); o["cum"]=f(df[curc[0]]); o["cum_prev"]=f(df[prvc[0]])
        elif "당기" in df.columns and "전기" in df.columns: o["ann"]=f(df["당기"]); o["ann_prev"]=f(df["전기"])
        else: continue
        out.append(o)
    return pd.concat(out)
Q=pd.concat([load(f) for f in sorted(glob.glob("dartfs/20*_[123]Q_PL_*.zip"))]).dropna(subset=["code","q"])
A=pd.concat([load(f) for f in sorted(glob.glob("dartfs/20*_4Q_PL_*.zip"))]).dropna(subset=["code","ann"])
for d in (Q,A): d.sort_values("cons",ascending=False,inplace=True); d.drop_duplicates(["code","qend"],inplace=True)
Q["qend"]=pd.to_datetime(Q.qend); A["qend"]=pd.to_datetime(A.qend)
# 4분기 = 연간 - 3분기 누적
q3=Q.copy(); q3["fy"]=q3.qend+pd.offsets.MonthEnd(3); m=A.merge(q3[["code","fy","cum","cum_prev"]],left_on=["code","qend"],right_on=["code","fy"],how="inner")
m["q"]=m.ann-m.cum; m["q_prev"]=m.ann_prev-m.cum_prev; m["lag"]=95
Q["lag"]=50; ALL=pd.concat([Q[["code","qend","q","q_prev","lag"]],m[["code","qend","q","q_prev","lag"]]]).dropna()
ALL=ALL[ALL.q_prev.abs()>1e8]; ALL["g"]=((ALL.q-ALL.q_prev)/ALL.q_prev.abs()).clip(-3,5); ALL.to_pickle("bt/qop.pkl")
print("분기 실적 레코드",len(ALL),"| 기간",ALL.qend.min().date(),"~",ALL.qend.max().date())
P=pd.read_pickle("bt/panel.pkl")
def attach(P,lag_override=None,name="OPG_Q"):
    E=ALL.copy(); E["avail"]=E.qend+pd.to_timedelta(E.lag if lag_override is None else np.where(E.lag==95,lag_override+45,lag_override),unit="D")
    E=E.sort_values("avail"); X=P[["date","code"]].sort_values("date")
    mg=pd.merge_asof(X,E[["code","avail","g"]],left_on="date",right_on="avail",by="code",direction="backward")
    mg["age"]=(mg.date-mg.avail).dt.days; mg.loc[mg.age>150,"g"]=np.nan
    return P.merge(mg[["date","code","g","age"]].rename(columns={"g":name,"age":name+"_age"}),on=["date","code"],how="left")
def spread(D,f,mask=None):
    D=D.dropna(subset=[f]); 
    if mask is not None: D=D[mask(D)]
    ls=[]
    for t,g in D.groupby("date"):
        if len(g)<50: continue
        b=pd.qcut(g[f].rank(method="first"),5,labels=False); r=g.groupby(b).fwd.mean(); ls.append(r[4]-r[0])
    ls=pd.Series(ls); return ls.mean()*100, ls.mean()/ls.std()*np.sqrt(len(ls)), (ls>0).mean()*100, len(ls)
D=attach(P)
print("\n[1] 같은 지표(영업이익 성장), 정보 갱신 주기만 다르게  → 월 수익차 / t / 맞은 달% / 개월")
print("  연 1회 갱신(결산 후 95일)  : %.2f%% / %.1f / %.0f%% / %d"%spread(D,"OPG"))
print("  분기마다 갱신(분기말 후 50일): %.2f%% / %.1f / %.0f%% / %d"%spread(D,"OPG_Q"))
print("\n[2] 분기 실적이 공개된 뒤 경과 기간별 (정보의 신선도)")
for lab,lo,hi in [("공개 후 0~30일",0,30),("31~60일",31,60),("61~90일",61,90),("91~150일",91,150)]:
    print("  %-12s: %.2f%% / t %.1f / %.0f%% / %d개월"%((lab,)+spread(D,"OPG_Q",lambda d:(d.OPG_Q_age>=lo)&(d.OPG_Q_age<=hi))))
print("\n[3] 정보를 더 빨리 얻는다고 가정하면 (분기말 후 N일에 전 종목 실적을 안다고 가정, 상한선 추정)")
for lag in [20,35,50,80]:
    D2=attach(P,lag,"X"); print("  분기말 후 %2d일: %.2f%% / t %.1f / %.0f%%"%((lag,)+spread(D2,"X")[:3]))
D.to_pickle("bt/panel_q.pkl")
