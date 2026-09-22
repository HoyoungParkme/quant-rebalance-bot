import requests, re, pandas as pd, time
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"})
S.get("https://kind.krx.co.kr/investwarn/delcompany.do?method=searchDelCompanyMain",timeout=20)
rows=[]
for p in range(1,12):
    r=S.post("https://kind.krx.co.kr/investwarn/delcompany.do",headers={"Referer":"https://kind.krx.co.kr/investwarn/delcompany.do?method=searchDelCompanyMain","X-Requested-With":"XMLHttpRequest"},
      data={"method":"searchDelCompanySub","currentPageSize":"100","pageIndex":str(p),"orderMode":"2","orderStat":"D","forward":"delcompany_sub","fromDate":"2019-01-01","toDate":"2026-09-18"},timeout=30)
    r.encoding="utf-8"; trs=re.findall(r'<tr[^>]*>(.*?)</tr>',r.text,flags=re.S)[1:]; n=0
    for tr in trs:
        c=re.search(r"companysummary_open\('(\d+)'\)",tr); tds=[re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',x)).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>',tr,flags=re.S)]
        if c and len(tds)>=4: rows.append({"code":c.group(1)+"0","name":tds[1],"del_date":tds[2],"reason":tds[3]}); n+=1
    if n==0: break
    time.sleep(0.4)
D=pd.DataFrame(rows).drop_duplicates("code"); 
def cls(r,n):
    if re.search("스팩|기업인수목적",n): return "스팩"
    if re.search("유가증권시장 상장|코스닥시장 상장|이전상장|시장 이전",r): return "이전상장"
    if re.search("합병|완전자회사|주식교환|지주회사|해산 사유",r): return "합병·자회사화"
    if re.search("신청|자진",r): return "자진폐지"
    if re.search("존속기간|존립기간|선박|투자회사|부동산투자",r+n): return "펀드·만기"
    return "부실·규정위반"
D["type"]=[cls(r,n) for r,n in zip(D.reason,D.name)]; D.to_csv("bt/delisted.csv",index=False)
print(len(D)); print(D.type.value_counts().to_string()); print(D[D.type=="부실·규정위반"].reason.str[:30].value_counts().head(12).to_string())
