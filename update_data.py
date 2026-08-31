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
REF_DATE = datetime.date(2026, 8, 7)           # 앱에 내장된 시가총액의 기준일
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
INVESCO_URL = ("https://www.invesco.com/us/financial-products/etfs/holdings/"
               "main/sitedetail/ajax?audienceType=Investor&action=download&ticker=QQQ")

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
            ref = close_on_or_before(sorted(h), REF_DATE)
            if ref:  # 시가총액 환산 배율 (내장 기준일 대비 주가 변화)
                live["ret"][t]["m"] = round(last_c / ref, 4)
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

    # 3.5) 새 실적 공시 감지 (SEC EDGAR 공식 접수 기록)
    try:
        sec_ua = {"User-Agent": "Q100App/1.0 (educational app; github.com/somh0914/q100)"}
        def sec_get(url):
            req = urllib.request.Request(url, headers=sec_ua)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        tick2cik = {}
        for row in sec_get("https://www.sec.gov/files/company_tickers.json").values():
            tick2cik[row["ticker"].upper()] = int(row["cik_str"])
        FOREIGN_ANNUAL_ONLY = {"ASML", "PDD", "ARM", "CCEP", "FER", "NBIS"}
        live.setdefault("fresh", {})
        cutoff = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        # 오래된 항목 정리
        for t in list(live["fresh"].keys()):
            if live["fresh"][t].get("d", "") < cutoff:
                del live["fresh"][t]
        found = 0
        for t in TICKERS:
            if t == "QQQ" or t not in tick2cik:
                continue
            try:
                sub = sec_get(f"https://data.sec.gov/submissions/CIK{tick2cik[t]:010d}.json")
                rec = sub["filings"]["recent"]
                want = ("20-F",) if t in FOREIGN_ANNUAL_ONLY else ("10-Q", "10-K")
                items_arr = rec.get("items") or [""] * len(rec["form"])
                best = None
                # 최신순 목록에서 실적 관련 공시를 찾음:
                # 10-Q/10-K(분기·연간 보고서) 또는 8-K 중 Item 2.02(실적 발표)
                for form, fdate, its in list(zip(rec["form"], rec["filingDate"], items_arr))[:60]:
                    is_earn = form in want or (
                        t not in FOREIGN_ANNUAL_ONLY and form == "8-K" and "2.02" in (its or ""))
                    if is_earn:
                        best = (fdate, form)
                        break
                if best and best[0] >= cutoff and best[0] > live["fresh"].get(t, {}).get("d", ""):
                    live["fresh"][t] = {"d": best[0], "f": best[1]}
                    found += 1
            except Exception:
                pass
            time.sleep(0.15)
        print(f"새 실적 공시 감지: {found}건 (현재 배지 {len(live['fresh'])}개)")
    except Exception as e:
        print("실적 공시 감지 실패 (기존 값 유지):", type(e).__name__)

    # 4) QQQ 편입 비중 (인베스코 공식 보유내역 CSV)
    try:
        import csv, io
        txt = http_get(INVESCO_URL, timeout=40)
        rows = list(csv.reader(io.StringIO(txt)))
        head = next(i for i, r in enumerate(rows)
                    if any("weight" in c.lower() for c in r) and any("ticker" in c.lower() for c in r))
        cols = [c.strip().lower() for c in rows[head]]
        ti = next(i for i, c in enumerate(cols) if "holding" in c and "ticker" in c)
        wi = next(i for i, c in enumerate(cols) if "weight" in c)
        raw = []
        for r in rows[head + 1:]:
            if len(r) <= max(ti, wi): continue
            sym = r[ti].strip().upper()
            try:
                w = float(r[wi].replace("%", "").replace(",", "").strip())
            except ValueError:
                continue
            if sym and w > 0:
                raw.append((sym, w))
        if len(raw) >= 80:
            comb = {}
            for sym, w in raw:
                key = "GOOGL" if sym in ("GOOG", "GOOGL") else sym
                comb[key] = comb.get(key, 0) + w
            hit = 0
            for t in TICKERS:
                if t != "QQQ" and t in comb:
                    live["ret"].setdefault(t, {})["w"] = round(comb[t], 2)
                    hit += 1
            raw.sort(key=lambda x: -x[1])
            live["top"] = [{"t": s, "w": round(w, 2)} for s, w in raw[:15]]
            live["top10"] = round(sum(w for _, w in raw[:10]), 1)
            print(f"QQQ 비중 갱신: {hit}개 기업 (보유내역 {len(raw)}종목)")
        else:
            print("QQQ 비중: 보유내역이 너무 적어 건너뜀 (기존 값 유지)")
    except Exception as e:
        print("QQQ 비중 수집 실패 (기존 값 유지):", type(e).__name__)

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
