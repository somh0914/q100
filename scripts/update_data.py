# -*- coding: utf-8 -*-
"""
Q100 데이터 자동 업데이트 스크립트 (GitHub Actions에서 매일 실행)
- 99개 기업 + QQQ + 나스닥100 지수 + 원달러 환율의 종가를 받아
- 연초대비(YTD)·1년 수익률을 계산해 live.json 파일로 저장합니다.
- 앱(index.html)은 열릴 때 live.json을 읽어 최신값으로 화면을 갱신합니다.
데이터 출처: Yahoo Finance (1순위) → Stooq (2순위). 외부 키 불필요.
"""
import json, time, sys, os, datetime, urllib.request, urllib.parse, urllib.error

TICKERS = [
 "NVDA","AAPL","GOOGL","MSFT","AMZN","MU","AMD","AVGO","META","TSLA",
 "WMT","INTC","CSCO","AMAT","COST","LRCX","PLTR","NFLX","PANW","TXN",
 "KLAC","LIN","AMGN","CRWD","SNDK","STX","PEP","TMUS","ADI","WDC",
 "SHOP","QCOM","GILD","MRVL","BKNG","ASML","ARM","ISRG","APP","FTNT","VRTX",
 "SBUX","ADP","ADBE","MELI","MAR","CSX","CEG","CDNS","DDOG","MNST",
 "CMCSA","INTU","DASH","ROST","CTAS","MDLZ","REGN","HON","SNPS","ORLY",
 "PCAR","AEP","MPWR","LITE","ABNB","WBD","PDD","TER","FANG","ALAB",
 "FAST","NXPI","BKR","CCEP","NBIS","AXON","PYPL","ADSK","EXC","XEL",
 "FER","IDXX","ODFL","MCHP","RKLB","PAYX","TTWO","CRWV","KDP","ROP",
 "TRI","WDAY","MSTR","DXCM","GEHC","KHC","ALNY","CPRT","QQQ",
]
YE_DATE = datetime.date(2025, 12, 31)          # 연초 기준일 (전년도 말)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def from_yahoo(sym):
    """Yahoo Finance v8 chart API → [(date, close), ...]"""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym) + "?range=15mo&interval=1d")
    data = json.loads(http_get(url))
    res = data["chart"]["result"][0]
    ts = res.get("timestamp") or []
    closes = res["indicators"]["quote"][0].get("close") or []
    out = []
    for t, c in zip(ts, closes):
        if c is not None:
            d = datetime.datetime.utcfromtimestamp(t).date()
            out.append((d, float(c)))
    return out

def from_stooq(sym):
    """Stooq CSV → [(date, close), ...]  (Date,Open,High,Low,Close,Volume)"""
    d1 = (datetime.date.today() - datetime.timedelta(days=430)).strftime("%Y%m%d")
    d2 = datetime.date.today().strftime("%Y%m%d")
    url = f"https://stooq.com/q/d/l/?s={sym}&d1={d1}&d2={d2}&i=d"
    txt = http_get(url)
    out = []
    for line in txt.strip().splitlines()[1:]:
        p = line.split(",")
        if len(p) >= 5:
            try:
                out.append((datetime.date.fromisoformat(p[0]), float(p[4])))
            except ValueError:
                pass
    return out

def get_history(yahoo_sym, stooq_sym):
    for fn, sym in ((from_yahoo, yahoo_sym), (from_stooq, stooq_sym)):
        for attempt in (1, 2):
            try:
                h = fn(sym)
                if len(h) >= 100:
                    return h
            except Exception:
                pass
            time.sleep(1.5)
    return None

def close_on_or_before(hist, d):
    best = None
    for hd, c in hist:
        if hd <= d:
            best = c
        else:
            break
    return best

def returns(hist):
    """(ytd%, y1%, 마지막종가, 마지막날짜)"""
    hist = sorted(hist)
    last_d, last_c = hist[-1]
    ye = close_on_or_before(hist, YE_DATE)
    y1 = close_on_or_before(hist, datetime.date.today() - datetime.timedelta(days=365))
    ytd = (last_c / ye - 1) * 100 if ye else None
    r1y = (last_c / y1 - 1) * 100 if y1 else None
    return ytd, r1y, last_c, last_d, ye

def main():
    live = {}
    if os.path.exists("live.json"):
        try:
            live = json.load(open("live.json", encoding="utf-8"))
        except Exception:
            live = {}
    live.setdefault("ret", {})

    ok, fail = 0, []
    latest_date = None

    # 1) 99개 기업 + QQQ
    for t in TICKERS:
        h = get_history(t, t.lower() + ".us")
        if not h:
            fail.append(t); continue
        ytd, r1y, last_c, last_d, ye = returns(h)
        if ytd is None:
            fail.append(t); continue
        if t == "QQQ":
            live["qqqNow"] = round(last_c, 2)
            if ye: live["qqqYE"] = round(ye, 2)
            latest_date = last_d
        else:
            live["ret"][t] = {"ytd": round(ytd, 1)}
            if r1y is not None:
                live["ret"][t]["y1"] = round(r1y, 1)
        ok += 1
        time.sleep(0.35)

    # 2) 나스닥100 지수
    h = get_history("^NDX", "^ndx")
    if h:
        ytd, r1y, last_c, last_d, ye = returns(h)
        live["ndxNow"] = round(last_c, 2)
        if ye: live["ndxYE"] = round(ye, 2)
        ok += 1
    else:
        fail.append("NDX")

    # 3) 원달러 환율
    h = get_history("KRW=X", "usdkrw")
    if h:
        _, _, last_c, _, ye = returns(h)
        live["fx"] = round(last_c, 1)
        if ye: live["fxYE"] = round(ye, 1)
        ok += 1
    else:
        fail.append("USDKRW")

    if latest_date:
        live["updated"] = latest_date.strftime("%Y.%m.%d")

    print(f"성공 {ok}개 / 실패 {len(fail)}개")
    if fail:
        print("실패 목록:", ", ".join(fail))

    if ok < 60:
        print("성공 개수가 너무 적어 live.json을 갱신하지 않습니다 (기존 데이터 유지).")
        sys.exit(1)

    with open("live.json", "w", encoding="utf-8") as f:
        json.dump(live, f, ensure_ascii=False, separators=(",", ":"))
    print("live.json 저장 완료 · 기준일:", live.get("updated", "?"))

if __name__ == "__main__":
    main()
