# -*- coding: utf-8 -*-
"""
Q100 데이터 자동 업데이트 스크립트 (GitHub Actions에서 매일 실행)
- 나스닥100 전체 101개 기업 + QQQ + 나스닥100 지수 + 원달러 환율의 종가를 받아
- 연초대비(YTD)·1년 수익률과 시가총액을 계산해 live.json 파일로 저장합니다.
- 신규 상장사(스페이스X·하니웰 에어로스페이스)는 연초 종가가 없어 YTD 대신
  시가총액·상장 후 데이터만 수집합니다.
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
 "TRI","WDAY","MSTR","DXCM","GEHC","KHC","ALNY","CPRT",
 "SPCX","HONA",  # 2026년 신규 편입 (스페이스X·하니웰 에어로스페이스) — 앱 반영 전이라도 데이터 선수집
 "QQQ",
]
# 연초 기준일 = 항상 "작년 12월 31일" — 해가 바뀌면 1월 첫 실행 때 자동으로 새 기준년도로 전환됨
YE_DATE = datetime.date(datetime.date.today().year - 1, 12, 31)
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
    # 최소 15거래일 — 신규 상장사(상장 몇 달)도 수집 가능하도록
    for fn, sym in ((from_yahoo, yahoo_sym), (from_stooq, stooq_sym)):
        for attempt in (1, 2):
            try:
                h = fn(sym)
                if len(h) >= 15:
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

def yahoo_caps(symbols):
    """야후에서 시가총액을 직접 받음 — 증자·자사주 소각에 따른 주식 수 변화가 자동 반영됨."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        opener.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=15)
    except Exception:
        pass  # 응답이 404여도 쿠키는 심어짐
    crumb = opener.open(urllib.request.Request(
        "https://query1.finance.yahoo.com/v1/test/getcrumb", headers=UA), timeout=15
    ).read().decode("utf-8", "replace").strip()
    caps = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
               + ",".join(chunk) + "&fields=marketCap&crumb=" + urllib.parse.quote(crumb))
        data = json.loads(opener.open(urllib.request.Request(url, headers=UA), timeout=25)
                          .read().decode("utf-8", "replace"))
        for q in data.get("quoteResponse", {}).get("result", []):
            mc = q.get("marketCap")
            if mc and mc > 1e9:
                caps[q.get("symbol", "").upper()] = round(mc / 1e9, 1)
        time.sleep(0.5)
    return caps


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
        if t == "QQQ":
            if ytd is None:
                fail.append(t); continue
            live["qqqNow"] = round(last_c, 2)
            if ye: live["qqqYE"] = round(ye, 2)
            latest_date = last_d
        else:
            ent = {}
            if ytd is not None:
                ent["ytd"] = round(ytd, 1)
            if r1y is not None:
                ent["y1"] = round(r1y, 1)
            ref = close_on_or_before(sorted(h), REF_DATE)
            if ref:  # 시가총액 환산 배율 (내장 기준일 대비 주가 변화)
                ent["m"] = round(last_c / ref, 4)
            if not ent:
                fail.append(t); continue
            old = live["ret"].get(t, {})
            old.update(ent)
            live["ret"][t] = old
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

    # 3.7) 시가총액 직접 갱신 (야후 — 주식 수 변화 자동 반영, 실패 시 주가 배율 방식 유지)
    try:
        caps = yahoo_caps([t for t in TICKERS if t != "QQQ"])
        capn = 0
        for t, b in caps.items():
            live["ret"].setdefault(t, {})["cap"] = b
            capn += 1
        print(f"시가총액 직접 수집: {capn}개 (야후 공시 기반)")
    except Exception as e:
        print("시가총액 직접 수집 실패 (주가 배율 방식으로 대체):", repr(e))

    # 3.5) 새 실적 공시 감지 (SEC EDGAR 공식 접수 기록)
    try:
        sec_ua = {"User-Agent": "Q100App/1.0 (educational app; github.com/somh0914/q100)",
                  "Accept-Encoding": "identity", "Host": "data.sec.gov"}
        def sec_get(url, tries=2):
            last = None
            for _ in range(tries):
                try:
                    h = dict(sec_ua)
                    h["Host"] = urllib.parse.urlparse(url).netloc
                    req = urllib.request.Request(url, headers=h)
                    with urllib.request.urlopen(req, timeout=25) as r:
                        return json.loads(r.read().decode("utf-8", "replace"))
                except Exception as e:
                    last = e; time.sleep(2)
            raise last
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
        print("실적 공시 감지 실패 (기존 값 유지):", repr(e))

    # 4) QQQ 편입 비중 (인베스코 공식 보유내역 CSV)
    try:
        import csv, io
        inv_h = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                 "Accept": "text/csv,application/csv,text/plain,*/*",
                 "Referer": "https://www.invesco.com/us/financial-products/etfs/product-detail?audienceType=Investor&ticker=QQQ"}
        txt = None
        for _ in range(2):
            try:
                req = urllib.request.Request(INVESCO_URL, headers=inv_h)
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                if "," in body and body.count("\n") > 50:
                    txt = body; break
            except Exception:
                pass
            time.sleep(3)
        if txt is None:
            raise RuntimeError("invesco_unreachable")
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

            # 5) 지수 편입·편출 감지 (보유내역이 충분히 완전할 때만)
            if len(raw) >= 95:
                cur = sorted(comb.keys())
                prev = live.get("members")
                today_s = datetime.date.today().isoformat()
                if prev:
                    added = [t for t in cur if t not in prev]
                    removed = [t for t in prev if t not in cur]
                    if added or removed:
                        chg = live.setdefault("chg", {"added": [], "removed": []})
                        for t in added:
                            if not any(x["t"] == t for x in chg["added"]):
                                chg["added"].append({"t": t, "d": today_s})
                        for t in removed:
                            if not any(x["t"] == t for x in chg["removed"]):
                                chg["removed"].append({"t": t, "d": today_s})
                        print(f"⚠️ 지수 변경 감지! 편입: {added} / 편출: {removed}")
                # 120일 지난 변경 기록 정리
                old = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
                if "chg" in live:
                    for k in ("added", "removed"):
                        live["chg"][k] = [x for x in live["chg"][k] if x.get("d", "") >= old]
                live["members"] = cur
        else:
            print("QQQ 비중: 보유내역이 너무 적어 건너뜀 (기존 값 유지)")
    except Exception as e:
        print("QQQ 비중 수집 실패 (기존 값 유지):", repr(e))
        # 예비 경로: 슬릭차트에서 구성종목 명단만 받아 편입·편출 감지 (비중은 갱신 안 함)
        try:
            import re
            req = urllib.request.Request("https://www.slickcharts.com/nasdaq100",
                                         headers={"User-Agent": UA["User-Agent"]})
            with urllib.request.urlopen(req, timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            syms = []
            for m in re.finditer(r'/symbol/([A-Z][A-Z0-9.\-]{0,6})', html):
                s = m.group(1)
                if s not in syms:
                    syms.append(s)
            if len(syms) >= 95:
                comb2 = sorted(set("GOOGL" if s in ("GOOG", "GOOGL") else s for s in syms))
                cur = comb2
                prev = live.get("members")
                today_s = datetime.date.today().isoformat()
                if prev:
                    added = [t for t in cur if t not in prev]
                    removed = [t for t in prev if t not in cur]
                    if added or removed:
                        chg = live.setdefault("chg", {"added": [], "removed": []})
                        for t in added:
                            if not any(x["t"] == t for x in chg["added"]):
                                chg["added"].append({"t": t, "d": today_s})
                        for t in removed:
                            if not any(x["t"] == t for x in chg["removed"]):
                                chg["removed"].append({"t": t, "d": today_s})
                        print(f"⚠️ 지수 변경 감지(예비 경로)! 편입: {added} / 편출: {removed}")
                live["members"] = cur
                print(f"구성종목 명단 확보(예비 경로 슬릭차트): {len(syms)}개 티커")
        except Exception as e2:
            print("예비 경로도 실패:", repr(e2))

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
