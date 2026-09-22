import requests, re, time, csv, sys
from urllib.parse import quote
S=requests.Session(); S.headers.update({"User-Agent":"Mozilla/5.0","Referer":"https://dart.fss.or.kr/dsab007/main.do"})
TYPES=["주요사항보고서(자기주식취득결정)","주요사항보고서(무상증자결정)","주요사항보고서(전환사채권발행결정)","주요사항보고서(유상증자결정)"]
PERIODS=[("20240101","20240630"),("20240701","20241231"),("20250101","20250630"),("20250701","20251231"),("20260101","20260831")]
def fetch(rn,s,e,page):
    data={"currentPage":page,"maxResults":100,"maxLinks":10,"sort":"date","series":"desc","reportNamePopYn":"Y","businessCode":"all","autoSearch":"N","option":"report","reportName":rn,"startDate":s,"endDate":e,"businessNm":"전체","corporationType":"all","closingAccountsMonth":"all"}
    for _ in range(3):
        try:
            r=S.post("https://dart.fss.or.kr/dsab007/detailSearch.ax",data=data,timeout=30); r.encoding="utf-8"; return r.text
        except Exception as ex: time.sleep(2)
    return ""
out=csv.writer(open("events_raw2.csv","w",newline="",encoding="utf-8")); out.writerow(["type","market","corp","title","date","rcp"])
for rn in TYPES:
    n=0
    for s,e in PERIODS:
        page=1
        while True:
            h=fetch(rn,s,e,page)
            m=re.search(r'\[(\d+)/(\d+)\]\s*\[총\s*([\d,]+)건\]',h)
            rows=re.findall(r'<tr[^>]*>(.*?)</tr>',h,flags=re.S)[1:]
            for r in rows:
                tds=re.findall(r'<td[^>]*>(.*?)</td>',r,flags=re.S)
                if len(tds)<5: continue
                mk=re.search(r'title="([^"]+)"',tds[1]); mk=mk.group(1) if mk else ""
                corp=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',tds[1])).strip()
                corp=re.sub(r'^(유|코|넥|기)\s+','',corp)
                title=re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',tds[2])).strip()
                rcp=re.search(r'rcpNo=(\d+)',tds[2]); rcp=rcp.group(1) if rcp else ""
                date=re.sub(r'<[^>]+>','',tds[4]).strip()
                out.writerow([rn,mk,corp,title,date,rcp]); n+=1
            if not m or int(m.group(1))>=int(m.group(2)): break
            page+=1; time.sleep(0.25)
    print(rn,n,flush=True)
