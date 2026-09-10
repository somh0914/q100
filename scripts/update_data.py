# -*- coding: utf-8 -*-
"""
Q100 데이터 자동 업데이트 스크립트 (GitHub Actions에서 매일 실행)
- 나스닥100 전체 101개 기업 + QQQ + 나스닥100 지수 + 원달러 환율의 종가를 받아
- 연초대비(YTD)·1년 수익률과 시가총액을 계산해 live.json 파일로 저장합니다.
- 신규 상장사(스페이스X·하니웰 에어로스페이스)는 연초 종가가 없어 YTD 대신
  시가총액·상장 후 데이터만 수집합니다.
- 새 실적 발표 감지 + 각 기업의 "다음 실적 발표 예정일"도 수집합니다 (🔔 표시용).
- 앱(index.html)은 열릴 때 live.json을 읽어 최신값으로 화면을 갱신합니다.
데이터 출처: 최신 종가·등락률·시가총액은 Yahoo 일괄 조회(요청 3번) → 이력(연초·1년 전 기준값)은
  Yahoo 차트(1순위) → Stooq(2순위). 외부 키 불필요.
- QQQ 편입 비중은 분기별 인베스코 CSV로 수동 반영 (매일 자동 수집 안 함).
"""
import json, time, sys, os, datetime, urllib.request, urllib.parse, urllib.error
from zoneinfo import ZoneInfo

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

# 쿠키를 유지하는 공용 커넥션 — 야후가 쿠키 없는 요청을 차단(429)할 때가 있어
# 시작할 때 한 번 쿠키를 받아두고 모든 요청에 함께 보낸다.
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
try:
    _opener.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=15)
except Exception:
    pass  # 응답이 404여도 쿠키는 심어짐

def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with _opener.open(req, timeout=timeout) as r:
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
            d = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).date()
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

def latest_trading_day_et():
    """미국 동부시간 기준 '가장 최근에 장이 끝난 거래일'.
    장마감(16:10 ET) 전이면 전일로, 주말이면 금요일로 물러난다."""
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    d = now.date()
    if (now.hour, now.minute) < (16, 10):
        d -= datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d

def get_history(yahoo_sym, stooq_sym):
    # 최소 15거래일 — 신규 상장사(상장 몇 달)도 수집 가능하도록.
    # 소스가 하루 늦은 데이터를 주면(예: 야후 차단→스투크 지연) 다른 소스와 비교해
    # 더 최신 종가를 가진 쪽을 쓴다 — '어제 종가' 대신 '오늘 종가'를 보장하기 위함.
    want = latest_trading_day_et()
    best = None
    for fn, sym in ((from_yahoo, yahoo_sym), (from_stooq, stooq_sym)):
        for attempt in (1, 2):
            try:
                h = fn(sym)
                if len(h) >= 15:
                    h = sorted(h)
                    if best is None or h[-1][0] > best[-1][0]:
                        best = h
                    break
            except Exception:
                pass
            time.sleep(1.5)
        if best is not None and best[-1][0] >= want:
            return best   # 최신 거래일 종가 확보 → 두 번째 소스 조회 불필요
    return best

def quote_close(q):
    """일괄 조회 결과에서 '확정된 최신 종가'를 뽑는다 → (날짜, 종가, 등락률%) 또는 None.
    장중(REGULAR)이면 아직 종가가 아니므로 쓰지 않는다. 우리 실행 시각은 모두 장 마감 뒤라
    보통 통과하지만, 수동 실행 등 장중에 돌 때를 대비한 안전장치."""
    if not q or not q.get("px") or not q.get("t"):
        return None
    if str(q.get("state") or "").upper() == "REGULAR":
        return None
    d = datetime.datetime.fromtimestamp(q["t"], ZoneInfo("America/New_York")).date()
    if d > latest_trading_day_et():
        return None
    return d, float(q["px"]), (float(q["chg"]) if q.get("chg") is not None else None)

def merge_quote(h, qc):
    """이력(h)의 마지막 날짜보다 일괄 조회 종가(qc)가 더 최신이면 이력 끝에 덧붙인다.
    → 차트 API가 막히거나 보조 소스가 하루 늦어도 '오늘 종가'는 일괄 조회 한 번으로 확보."""
    if not qc:
        return h, False
    d, c, _ = qc
    h = sorted(h) if h else []
    if h and h[-1][0] >= d:
        return h, False
    return h + [(d, c)], True

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

def yahoo_quotes(symbols):
    """야후 일괄 조회 — 시가총액(주식 수 변화 자동 반영) + 실적 발표일을 한 번에 받음."""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    try:
        opener.open(urllib.request.Request("https://fc.yahoo.com", headers=UA), timeout=15)
    except Exception:
        pass  # 응답이 404여도 쿠키는 심어짐
    crumb = opener.open(urllib.request.Request(
        "https://query1.finance.yahoo.com/v1/test/getcrumb", headers=UA), timeout=15
    ).read().decode("utf-8", "replace").strip()
    out = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        url = ("https://query1.finance.yahoo.com/v7/finance/quote?symbols="
               + ",".join(chunk)
               + "&fields=marketCap,earningsTimestamp,earningsTimestampEnd,"
                 "regularMarketPrice,regularMarketChangePercent,regularMarketPreviousClose,"
                 "regularMarketTime,marketState&crumb="
               + urllib.parse.quote(crumb))
        data = json.loads(opener.open(urllib.request.Request(url, headers=UA), timeout=25)
                          .read().decode("utf-8", "replace"))
        for q in data.get("quoteResponse", {}).get("result", []):
            sym = q.get("symbol", "").upper()
            if sym:
                out[sym] = {"cap": q.get("marketCap"),
                            "ets": q.get("earningsTimestamp"),
                            "px": q.get("regularMarketPrice"),
                            "chg": q.get("regularMarketChangePercent"),
                            "pc": q.get("regularMarketPreviousClose"),
                            "t": q.get("regularMarketTime"),
                            "state": q.get("marketState")}
        time.sleep(0.5)
    return out



# 각 기업의 회계연도 종료월 (없으면 12월). 4Q(연간) 실적 발표를 가려내는 데 사용
FYEND = {"AAPL": 9, "ADBE": 11, "ADI": 11, "ADP": 6, "ADSK": 1, "AMAT": 10, "ARM": 3, "AVGO": 10, "COST": 8, "CPRT": 7, "CRWD": 1, "CSCO": 7, "CTAS": 5, "INTU": 7, "KLAC": 6, "LITE": 6, "LRCX": 6, "MCHP": 3, "MRVL": 1, "MSFT": 6, "MU": 8, "NVDA": 1, "PANW": 7, "PAYX": 5, "QCOM": 9, "ROST": 1, "SBUX": 9, "SNDK": 6, "SNPS": 10, "STX": 6, "TTWO": 3, "WDAY": 1, "WDC": 6, "WMT": 1}

def is_fy_close(ticker, rep_date):
    """그 발표가 '회계연도 마지막 분기(연간 확정)' 발표인지 판정.
    회계연도 종료 후 10~80일 사이에 나온 발표면 4Q로 본다."""
    m = FYEND.get(ticker, 12)
    for back in (0, 1):
        y = rep_date.year - back
        end = datetime.date(y + (1 if m == 12 else 0), 1, 1) - datetime.timedelta(days=1) \
              if m == 12 else datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)
        gap = (rep_date - end).days
        if 10 <= gap <= 80:
            return end.year if m >= 6 else end.year   # 확정된 회계연도
    return None

def main():
    live = {}
    if os.path.exists("live.json"):
        try:
            live = json.load(open("live.json", encoding="utf-8"))
        except Exception:
            live = {}
    live.setdefault("ret", {})
    # 시세 역행 방지용 스냅샷 — 이번 수집이 기존보다 오래된 날짜면 되돌린다.
    prev_updated = str(live.get("updated") or "")
    prev_ret = json.loads(json.dumps(live.get("ret", {})))
    prev_px = {k: live.get(k) for k in ("qqqNow", "qqqYE", "ndxNow", "ndxYE", "fx", "fxYE")}
    # 예전 인베스코 자동 수집이 남긴 비중 값 제거 — 비중은 앱 내장 분기 표만 쓴다
    for _t, _e in live["ret"].items():
        if isinstance(_e, dict):
            _e.pop("w", None)
    live.pop("top", None)
    live.pop("top10", None)
    # 이전 실행에서 명단에 섞여 들어온 ETF 티커 청소 (가짜 편출 알림 방지)
    if isinstance(live.get("members"), list):
        _bad = {"SPY", "DIA", "IWM", "VOO", "VTI", "QQQM", "ONEQ", "TQQQ", "SQQQ", "QQQ"}
        live["members"] = [t for t in live["members"] if t not in _bad]

    ok, fail = 0, []
    latest_date = None

    # 0.5) 지수에 새로 편입된 종목도 자동으로 수집 대상에 포함
    #      (어제까지 확보한 구성종목 명단과 내장 TICKERS의 합집합 —
    #       앱에 기업 페이지가 만들어지기 전이라도 주가·시총·실적일 데이터가 먼저 쌓임)
    fetch_list = list(TICKERS)
    if isinstance(live.get("members"), list):
        extra = [t for t in live["members"]
                 if t not in fetch_list and t.replace("-", "").isalpha() and len(t) <= 6]
        if extra:
            fetch_list += extra
            print("구성종목 명단 기반 자동 추가 수집:", ", ".join(extra))

    # 0.7) 야후 일괄 조회를 먼저 — 101개 종목의 최신 종가·등락률·시가총액을 요청 3번으로 확보.
    #      (종목별 차트 요청은 횟수 제한에 자주 막히고 보조 소스는 하루 늦지만,
    #       일괄 조회는 잘 통과되므로 '오늘 종가'는 여기서 가져오고 이력은 기준값 계산에만 쓴다)
    quotes = {}
    try:
        quotes = yahoo_quotes(list(fetch_list))
        qn = sum(1 for t in fetch_list if quote_close(quotes.get(t)))
        print(f"일괄 조회: {len(quotes)}개 응답 · 확정 종가 {qn}개")
    except Exception as e:
        print("야후 일괄 조회 실패 — 종목별 이력만 사용:", repr(e))

    # 1) 전체 기업 + QQQ
    for t in fetch_list:
        h0 = get_history(t, t.lower() + ".us")
        qc = quote_close(quotes.get(t))
        h, used_q = merge_quote(h0, qc)
        if not h:
            fail.append(t); continue
        if not h0:
            # 이력은 두 소스 모두 실패했지만 일괄 조회 종가는 있음 → 주가·등락률만 갱신하고
            # 연초대비·1년 수익률·환산배율은 기존 값을 유지 (상장 직후로 오인하지 않도록)
            if t == "QQQ":
                live["qqqNow"] = round(qc[1], 2); latest_date = qc[0]
            else:
                old = live["ret"].get(t, {})
                old["p"] = round(qc[1], 2)
                if qc[2] is not None: old["dc"] = round(qc[2], 2)
                live["ret"][t] = old
            ok += 1
            continue
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
            else:
                # 연중 상장 기업: 첫 상장일 종가를 기준으로 수익률 계산
                # (다음 해 1월부터는 연초 종가가 생기므로 자동으로 위의 일반 YTD로 전환됨)
                first_d, first_c = sorted(h)[0]
                if first_d > YE_DATE and first_c > 0:
                    ent["ytd"] = round((last_c / first_c - 1) * 100, 1)
                    ent["ipo"] = first_d.isoformat()
            if r1y is not None:
                ent["y1"] = round(r1y, 1)
            ent["p"] = round(last_c, 2)   # 최신 종가 (앱의 주가 표시줄용)
            hs = sorted(h)
            if used_q and qc[2] is not None:    # 전일 대비 등락률 — 일괄 조회의 공식 값 우선
                ent["dc"] = round(qc[2], 2)
            elif len(hs) >= 2 and hs[-2][1]:
                ent["dc"] = round((last_c / hs[-2][1] - 1) * 100, 2)
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

    # 3.5) 야후 일괄 조회 — 시가총액 갱신 + 새 실적 발표 감지 + 다음 발표 예정일 (같은 통로)
    #      * SEC는 GitHub 서버 접속을 차단(403)하여 야후의 실적 발표일 데이터로 대체
    try:
        if not quotes:
            quotes = yahoo_quotes([t for t in fetch_list if t != "QQQ"])
        live.setdefault("fresh", {})
        cutoff = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
        for t in list(live["fresh"].keys()):
            if live["fresh"][t].get("d", "") < cutoff:
                del live["fresh"][t]
        prev_next = live.get("next") if isinstance(live.get("next"), dict) else {}
        new_next = {}
        capn = ern = 0
        now = datetime.datetime.now(datetime.timezone.utc)
        today_d = datetime.date.today()
        for t, q in quotes.items():
            if t == "QQQ":
                continue
            mc = q.get("cap")
            if mc and mc > 1e9:
                live["ret"].setdefault(t, {})["cap"] = round(mc / 1e9, 1)
                capn += 1
            ets = q.get("ets")
            if ets:
                edt = datetime.datetime.fromtimestamp(ets, datetime.timezone.utc)
                days_ago = (now - edt).total_seconds() / 86400
                # 발표일이 지난 5일 이내면 "새 실적 발표"로 기록
                if 0 <= days_ago <= 5:
                    d = edt.date().isoformat()
                    if d > live["fresh"].get(t, {}).get("d", ""):
                        live["fresh"][t] = {"d": d, "f": "발표"}
                        ern += 1
                # 미래 예정일이면 "다음 실적 발표일"로 저장 (앱의 🔔 다음 실적 표시용)
                elif days_ago < 0 and -days_ago <= 200:
                    new_next[t] = edt.date().isoformat()
        # 엔비디아형 사각지대 보완: 발표 직후 야후가 날짜를 곧바로 다음 분기로
        # 바꿔버리면 위의 "지난 5일" 검사에 안 걸림 → 어제까지 저장해 둔
        # 예정일이 방금 지났으면 그 날을 발표일로 간주해 감지한다.
        for t, d in prev_next.items():
            try:
                gap = (today_d - datetime.date.fromisoformat(d)).days
            except Exception:
                continue
            if 0 < gap <= 7:
                if d > live["fresh"].get(t, {}).get("d", ""):
                    live["fresh"][t] = {"d": d, "f": "발표"}
                    ern += 1
            elif gap <= 0 and t not in new_next:
                new_next[t] = d  # 아직 미래인 예정일은 유지 (야후 일시 누락 대비)
        live["next"] = new_next
        # 회계연도 마지막 분기(=연간 실적 확정) 발표를 따로 표시 → 앱의 연간 확정/전망 갱신 신호
        fyq4 = live.get("fyq4") or {}
        old_fyq4 = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
        fyq4 = {k: v for k, v in fyq4.items() if isinstance(v, dict) and v.get("d", "") >= old_fyq4}
        for t, info in live["fresh"].items():
            try:
                rd = datetime.date.fromisoformat(info.get("d", ""))
            except Exception:
                continue
            closed = is_fy_close(t, rd)
            if closed and fyq4.get(t, {}).get("d", "") < info["d"]:
                fyq4[t] = {"d": info["d"], "fy": closed}
        live["fyq4"] = fyq4
        if fyq4:
            print("연간 실적 확정(4Q) 발표:", ", ".join(f"{k}(FY{str(v['fy'])[2:]})" for k, v in fyq4.items()))
        print(f"시가총액 {capn}개 갱신 · 새 실적 발표 감지 {ern}건 (현재 배지 {len(live['fresh'])}개) · 다음 발표 예정일 {len(new_next)}개 확보")
    except Exception as e:
        print("야후 일괄 조회 실패 (기존 값 유지):", repr(e))

    # 4) 지수 편입·편출 감지 — 슬릭차트 구성종목 명단 (매일)
    #    * QQQ 편입 "비중"은 여기서 갱신하지 않는다. 비중은 분기별(3·6·9·12월 셋째 금요일
    #      리밸런싱 직후) 인베스코 공식 CSV를 받아 앱에 직접 반영하는 절차로 관리한다.
    #      (인베스코 사이트는 GitHub 서버의 자동 요청을 봇으로 차단하므로 매일 시도해 봐야
    #       실패만 반복된다 → 시도 자체를 제거)
    try:
        import re
        req = urllib.request.Request("https://www.slickcharts.com/nasdaq100",
                                     headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        NOT_STOCK = {"SPY", "DIA", "IWM", "VOO", "VTI", "QQQM", "ONEQ", "TQQQ", "SQQQ", "QQQ"}
        syms = []
        for m in re.finditer(r'/symbol/([A-Z][A-Z0-9.\-]{0,6})', html):
            s = m.group(1)
            if s not in syms and s not in NOT_STOCK:
                syms.append(s)
        if 95 <= len(syms) <= 110:
            cur = sorted(set("GOOGL" if s in ("GOOG", "GOOGL") else s for s in syms))
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
            print(f"구성종목 명단 확보(슬릭차트): {len(cur)}개 티커")
        else:
            print(f"구성종목 명단 수집 건너뜀 — 티커 수 비정상({len(syms)}개), 기존 명단 유지")
    except Exception as e:
        print("구성종목 명단 수집 실패 (기존 명단 유지):", repr(e))

    # ── 시세 역행 방지 ──────────────────────────────────────────────
    # 백업 실행에서 야후가 막히면 Stooq(하루 지연) 값만 남아 이미 저장해 둔
    # 최신 종가를 하루 묵은 값으로 덮어쓰는 사고가 난다. 이번 수집 기준일이
    # 기존 저장분보다 과거면 주가·환율·지수는 기존 값을 그대로 지킨다.
    # (시가총액·실적 발표일·구성종목은 최신 수집분을 그대로 반영)
    new_updated = latest_date.strftime("%Y.%m.%d") if latest_date else ""
    if prev_updated and new_updated and new_updated < prev_updated:
        print(f"⚠️ 이번 수집 기준일({new_updated})이 기존 저장분({prev_updated})보다 과거 "
              f"— 주가·환율·지수는 기존 최신값을 유지합니다 (시세 역행 방지)")
        for t, old_e in prev_ret.items():
            cur = live["ret"].setdefault(t, {})
            for k in ("ytd", "y1", "m", "p", "dc"):
                if k in old_e:
                    cur[k] = old_e[k]
        for k, v in prev_px.items():
            if v is not None:
                live[k] = v
        live["updated"] = prev_updated
    elif latest_date:
        live["updated"] = new_updated

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
