from curl_cffi import requests as cr
from bs4 import BeautifulSoup
h={"User-Agent":"Mozilla/5.0","Referer":"https://www.underhentai.net/"}
html=cr.get("https://www.underhentai.net/watch/?id=11135&ep=0",headers=h,impersonate="chrome120",timeout=45).text
soup=BeautifulSoup(html,"lxml")
links=[]
for a in soup.select("a[href]"):
    href=a.get("href") or ""
    if "cheat-item" in href or (href.startswith("/") and href.count("/")<=2 and href not in ("/","/index/","/watch/")):
        links.append(href)
print(links[:20])
print("title", soup.select_one("h1").get_text(strip=True) if soup.select_one("h1") else None)
