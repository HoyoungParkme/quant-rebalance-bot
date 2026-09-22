import pandas as pd, numpy as np, warnings; warnings.filterwarnings("ignore")
P=pd.read_pickle("bt/panel.pkl"); pd.set_option("display.width",250); pd.set_option("display.float_format",lambda v:f"{v:,.2f}")
facs=[f for f in ["EP","BP","SP","ROE","OPG","MOM12_1","MOM6_1","MOM3","REV1","HIGH52","VOL60","VOLSURGE","TURN20","SIZE","FRG20","FRG60","FRGR60","ORG20","ORG60","IND20","IND60"] if f in P]
res=[]
for f in facs:
    q=[];ic=[]
    for t,g in P.dropna(subset=[f]).groupby("date"):
        if len(g)<100: continue
        b=pd.qcut(g[f].rank(method="first"),5,labels=False)
        r=g.groupby(b).fwd.mean(); r["date"]=t; r["ic"]=g[f].corr(g.fwd,method="spearman"); q.append(r)
    Q=pd.DataFrame(q).set_index("date"); ls=Q[4]-Q[0]
    yr=ls.groupby(ls.index.year).mean()*100
    row={"지표":f,"Q1(하위)":Q[0].mean()*100,"Q3":Q[2].mean()*100,"Q5(상위)":Q[4].mean()*100,"Q5-Q1":ls.mean()*100,"t":ls.mean()/ls.std()*np.sqrt(len(ls)),"승률%":(ls>0).mean()*100,"IC":Q.ic.mean(),"IC_t":Q.ic.mean()/Q.ic.std()*np.sqrt(len(Q)),"개월":len(ls)}
    for y in range(2020,2027): row[str(y)]=yr.get(y,np.nan)
    res.append(row)
T=pd.DataFrame(res).set_index("지표"); T.to_csv("bt/factor_results.csv")
print("월별 5분위 다음달 수익률(%), 동일가중. Q5-Q1 = 지표 상위 20% - 하위 20%"); print(T.to_string())
