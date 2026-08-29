
# -*- coding: utf-8 -*-
"""
Kuwait Stock Smart Scanner v1.0
برنامج تحليلي لبورصة الكويت:
- يجلب قائمة الشركات من تقرير الأسهم الحرة الرسمي لبورصة الكويت.
- يجلب الإفصاحات الرسمية من RSS بورصة الكويت (عربي + إنجليزي).
- يجلب الأسعار اليومية من Yahoo Finance عبر yfinance (قد تكون مؤخرة).
- يحلل الاتجاه، الزخم، السيولة، الأخبار، ويصنف أفضل فرص الشراء.
- لا ينفذ أوامر شراء/بيع.
"""

import re
import math
import time
import html
import urllib.parse
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests
import feedparser
import yfinance as yf
import streamlit as st


APP_NAME = "Kuwait Stock Smart Scanner v1.0"
BOURSA_RSS_EN = "https://rss.boursakuwait.com.kw/rss/FeedFull.aspx?T=4"
BOURSA_RSS_AR = "https://rss.boursakuwait.com.kw/A/rss/FeedFull.aspx?T=4"
USER_AGENT = "Mozilla/5.0 (KuwaitStockSmartScanner/1.0)"

# قائمة احتياطية لأكبر أسهم السوق الأول في حال تعذر قراءة تقرير البورصة.
FALLBACK = [
    ("Premier","101","NBK","NATIONAL BANK OF KUWAIT"),
    ("Premier","102","GBK","GULF BANK"),
    ("Premier","104","ABK","AL-AHLI BANK OF KUWAIT"),
    ("Premier","106","KIB","KUWAIT INTERNATIONAL BANK"),
    ("Premier","107","BURG","BURGAN BANK"),
    ("Premier","108","KFH","KUWAIT FINANCE HOUSE"),
    ("Premier","109","BOUBYAN","BOUBYAN BANK"),
    ("Premier","201","KINV","KUWAIT INVESTMENT COMPANY"),
    ("Premier","203","IFA","INTERNATIONAL FINANCIAL ADVISERS HOLDING"),
    ("Premier","204","NINV","NATIONAL INVESTMENTS COMPANY"),
    ("Premier","205","KPROJ","KUWAIT PROJECTS COMPANY"),
    ("Premier","212","ARZAN","ARZAN FINANCIAL GROUP"),
    ("Premier","222","AAYAN","AAYAN LEASING & INVESTMENT"),
    ("Premier","401","KRE","KUWAIT REAL ESTATE COMPANY"),
    ("Premier","402","URC","UNITED REAL ESTATE COMPANY"),
    ("Premier","404","SRE","SALHIA REAL ESTATE COMPANY"),
    ("Premier","413","MABANEE","MABANEE COMPANY"),
    ("Premier","418","ALTIJARIA","THE COMMERCIAL REAL ESTATE CO."),
    ("Premier","501","NIND","NATIONAL INDUSTRIES GROUP"),
    ("Premier","505","CABLE","GULF CABLES AND ELECTRICAL INDUSTRIES"),
    ("Premier","506","SHIP","HEAVY ENGINEERING INDUSTRIES AND SHIP BUILDING"),
    ("Premier","514","BPCC","BOUBYAN PETROCHEMICAL"),
    ("Premier","603","MKHZN","AGILITY PUBLIC WAREHOUSING"),
    ("Premier","605","ZAIN","MOBILE TELECOMMUNICATIONS COMPANY"),
    ("Premier","623","HUMANSOFT","HUMANSOFT HOLDING"),
    ("Premier","634","IFAHR","IFA HOTELS & RESORTS"),
    ("Premier","635","CGC","COMBINED GROUP CONTRACTING"),
    ("Premier","645","OULAFUEL","OULA FUEL MARKETING"),
    ("Premier","654","JAZEERA","JAZEERA AIRWAYS"),
    ("Premier","813","GFH","GFH BANK"),
    ("Premier","821","WARBABANK","WARBA BANK"),
    ("Premier","822","STC","KUWAIT TELECOMMUNICATIONS"),
    ("Premier","823","MEZZAN","MEZZAN HOLDING"),
    ("Premier","824","INTEGRATED","INTEGRATED HOLDING"),
    ("Premier","827","BOURSA","BOURSA KUWAIT SECURITIES"),
    ("Premier","830","ALG","ALI ALGHANIM SONS AUTOMOTIVE"),
    ("Premier","831","BEYOUT","BEYOUT HOLDING"),
    ("Premier","832","ALFTAQA","ACTION ENERGY"),
    ("Premier","833","TROLLEY","TROLLEY GENERAL TRADING"),
]

POSITIVE_WORDS = {
    "profit": 1.5, "profits": 1.5, "growth": 1.2, "increase": 0.7, "increased": 0.7,
    "dividend": 1.5, "distribution": 0.8, "contract": 1.0, "award": 1.0,
    "upgrade": 1.5, "acquisition": 0.8, "approval": 0.6, "record": 0.5,
    "positive": 0.8, "expansion": 0.7, "partnership": 0.6,
    "ربح": 1.5, "أرباح": 1.5, "الارباح": 1.5, "الأرباح": 1.5, "نمو": 1.2,
    "ارتفاع": 0.7, "زيادة": 0.7, "توزيع": 1.0, "توزيعات": 1.2, "منحة": 1.0,
    "عقد": 1.0, "ترسية": 1.2, "مناقصة": 0.7, "رفع التصنيف": 1.5,
    "استحواذ": 0.8, "موافقة": 0.6, "توسع": 0.7, "شراكة": 0.6
}
NEGATIVE_WORDS = {
    "loss": -1.8, "losses": -1.8, "decline": -0.9, "decrease": -0.8,
    "suspension": -2.0, "suspended": -2.0, "lawsuit": -1.4, "court": -0.7,
    "downgrade": -1.8, "default": -2.5, "warning": -1.0, "impairment": -1.2,
    "خسارة": -1.8, "خسائر": -1.8, "انخفاض": -0.9, "تراجع": -0.9,
    "إيقاف": -2.0, "ايقاف": -2.0, "وقف التداول": -2.0, "دعوى": -1.4,
    "دعاوى": -1.4, "حكم": -0.8, "خفض التصنيف": -1.8, "تعثر": -2.5,
    "تحذير": -1.0, "مخصصات": -0.7
}

st.set_page_config(page_title=APP_NAME, page_icon="📊", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; text-align: right; }
[data-testid="stDataFrame"] { direction: rtl; }
.small-note {font-size:0.9rem; opacity:0.8;}
.good {font-weight:700;}
.block-container { padding-top: 1rem; padding-left: .8rem; padding-right: .8rem; max-width: 100%; }
h1 { font-size: 1.65rem !important; line-height: 1.25 !important; }
h2 { font-size: 1.25rem !important; }
h3 { font-size: 1.1rem !important; }
[data-testid="stMetricValue"] { font-size: 1.35rem !important; }
button[kind="primary"] { min-height: 48px; font-size: 1rem; }
[data-testid="stSidebar"] { direction: rtl; }
@media (max-width: 700px) {
  .block-container { padding-top: .65rem; padding-left: .55rem; padding-right: .55rem; }
  h1 { font-size: 1.45rem !important; }
  [data-testid="column"] { width: 100% !important; flex: 1 1 100% !important; }
  div[data-testid="stMetric"] { padding: .55rem .35rem; }
  .stButton button { width: 100%; min-height: 48px; }
  [data-testid="stDataFrame"] { font-size: 0.82rem; }
}
</style>
""", unsafe_allow_html=True)


def safe_float(x, default=np.nan):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def normalize_text(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.lower()
    s = re.sub(r"[\u064B-\u065F\u0670]", "", s)
    s = s.replace("أ","ا").replace("إ","ا").replace("آ","ا")
    s = re.sub(r"[^0-9a-z\u0600-\u06FF]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def news_sentiment(text):
    t = normalize_text(text)
    score = 0.0
    for k, v in POSITIVE_WORDS.items():
        if normalize_text(k) in t:
            score += v
    for k, v in NEGATIVE_WORDS.items():
        if normalize_text(k) in t:
            score += v
    return max(-6.0, min(6.0, score))


def latest_completed_quarter_url():
    now = datetime.now()
    q_month = ((now.month - 1) // 3) * 3
    year = now.year
    if q_month == 0:
        q_month = 12
        year -= 1
    return f"https://reports.boursakuwait.com.kw/en/indicativefreefloat/{q_month}/{year}"


@st.cache_data(ttl=86400, show_spinner=False)
def load_universe():
    url = latest_completed_quarter_url()
    try:
        headers = {"User-Agent": USER_AGENT}
        tables = pd.read_html(requests.get(url, headers=headers, timeout=20).text)
        table = None
        for df in tables:
            cols = [str(c).strip().lower() for c in df.columns]
            if "ticker" in cols and "name" in cols:
                table = df.copy()
                break
        if table is None:
            raise RuntimeError("لم يتم العثور على جدول الشركات")
        # توحيد الأعمدة
        cmap = {str(c).strip().lower(): c for c in table.columns}
        out = pd.DataFrame({
            "Market": table[cmap["market"]].astype(str),
            "SecCode": table[cmap["seccode"]].astype(str),
            "Ticker": table[cmap["ticker"]].astype(str).str.strip(),
            "Name": table[cmap["name"]].astype(str).str.strip(),
        })
        ff_pct_col = next((c for c in table.columns if "free float %" in str(c).lower()), None)
        ff_val_col = next((c for c in table.columns if "free float value" in str(c).lower()), None)
        out["FreeFloatPct"] = pd.to_numeric(
            table[ff_pct_col].astype(str).str.replace("%","",regex=False).str.replace(",","",regex=False),
            errors="coerce") if ff_pct_col is not None else np.nan
        out["FreeFloatValue"] = pd.to_numeric(
            table[ff_val_col].astype(str).str.replace(",","",regex=False),
            errors="coerce") if ff_val_col is not None else np.nan
        out = out.dropna(subset=["Ticker"]).drop_duplicates("Ticker").reset_index(drop=True)
        return out, url, False
    except Exception:
        out = pd.DataFrame(FALLBACK, columns=["Market","SecCode","Ticker","Name"])
        out["FreeFloatPct"] = np.nan
        out["FreeFloatValue"] = np.nan
        return out, url, True


@st.cache_data(ttl=600, show_spinner=False)
def load_boursa_rss():
    items = []
    for lang, url in [("EN", BOURSA_RSS_EN), ("AR", BOURSA_RSS_AR)]:
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
            feed = feedparser.parse(r.content)
            for e in feed.entries[:600]:
                title = str(getattr(e, "title", ""))
                summary = str(getattr(e, "summary", getattr(e, "description", "")))
                link = str(getattr(e, "link", ""))
                published = str(getattr(e, "published", getattr(e, "updated", "")))
                items.append({
                    "lang": lang, "title": title, "summary": summary,
                    "link": link, "published": published,
                    "text_norm": normalize_text(title + " " + summary),
                    "sentiment": news_sentiment(title + " " + summary)
                })
        except Exception:
            pass
    # إزالة التكرار
    uniq = {}
    for x in items:
        key = (normalize_text(x["title"]), x["link"])
        uniq[key] = x
    return list(uniq.values())


def match_official_news(ticker, name, rss_items, max_items=12):
    tick = normalize_text(ticker)
    name_norm = normalize_text(name)
    name_tokens = [x for x in name_norm.split() if len(x) >= 4]
    matched = []
    for x in rss_items:
        tx = x["text_norm"]
        ticker_hit = re.search(rf"(^|\s){re.escape(tick)}(\s|$)", tx) is not None
        token_hits = sum(1 for tok in name_tokens[:6] if tok in tx)
        name_hit = token_hits >= min(2, max(1, len(name_tokens)))
        if ticker_hit or name_hit:
            matched.append(x)
    return matched[:max_items]


@st.cache_data(ttl=900, show_spinner=False)
def google_news(company_name, ticker, max_items=6):
    q = f'"{company_name}" OR "{ticker}" Kuwait stock'
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en&gl=KW&ceid=KW:en"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=12)
        feed = feedparser.parse(r.content)
        out = []
        for e in feed.entries[:max_items]:
            title = str(getattr(e, "title", ""))
            summary = str(getattr(e, "summary", ""))
            out.append({
                "title": title,
                "summary": summary,
                "link": str(getattr(e, "link", "")),
                "published": str(getattr(e, "published", "")),
                "sentiment": news_sentiment(title + " " + summary),
            })
        return out
    except Exception:
        return []


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out


def atr(df, period=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    prev = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_indicators(df):
    df = df.dropna().copy()
    if len(df) < 60:
        return None
    c = df["Close"].astype(float)
    v = df["Volume"].astype(float)
    ema20 = c.ewm(span=20, adjust=False).mean()
    ema50 = c.ewm(span=50, adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    r = rsi(c)
    a = atr(df)
    last = c.iloc[-1]
    low20 = c.tail(20).min()
    high20 = c.tail(20).max()
    low60 = c.tail(60).min()
    high60 = c.tail(60).max()
    high252 = c.tail(min(252, len(c))).max()
    low252 = c.tail(min(252, len(c))).min()
    mom20 = (last / c.iloc[-21] - 1) * 100 if len(c) > 21 else 0
    vol20 = v.tail(20).mean()
    vol_ratio = (v.iloc[-1] / vol20) if vol20 and not np.isnan(vol20) else 1.0
    return {
        "price": last,
        "ema20": ema20.iloc[-1], "ema50": ema50.iloc[-1], "ema200": ema200.iloc[-1],
        "rsi": r.iloc[-1], "macd": macd.iloc[-1], "macd_signal": macd_sig.iloc[-1],
        "atr": a.iloc[-1], "mom20": mom20, "vol_ratio": vol_ratio,
        "support20": low20, "support60": low60, "res20": high20, "res60": high60,
        "high52": high252, "low52": low252,
        "avg_vol20": vol20,
        "daily_value_est": float(last * vol20) if vol20 and not np.isnan(vol20) else np.nan,
    }


def technical_score(ind):
    if not ind:
        return 0, []
    s = 0.0
    reasons = []
    p, e20, e50, e200 = ind["price"], ind["ema20"], ind["ema50"], ind["ema200"]
    # 50 نقطة
    if p > e20 > e50:
        s += 15; reasons.append("الاتجاه القصير صاعد")
    elif p > e20:
        s += 10; reasons.append("السعر فوق EMA20")
    elif p > e50:
        s += 6
    if e50 > e200:
        s += 8; reasons.append("الاتجاه المتوسط إيجابي")
    rr = ind["rsi"]
    if 48 <= rr <= 68:
        s += 9; reasons.append("RSI في منطقة صحية")
    elif 40 <= rr < 48 or 68 < rr <= 73:
        s += 6
    elif rr < 30:
        s += 5; reasons.append("تشبع بيعي محتمل")
    if ind["macd"] > ind["macd_signal"]:
        s += 7; reasons.append("MACD إيجابي")
    if ind["mom20"] > 0:
        s += min(6, 3 + ind["mom20"]/4); reasons.append("زخم 20 يوم موجب")
    if ind["vol_ratio"] >= 1.15:
        s += 4; reasons.append("نشاط حجم تداول أعلى من المتوسط")
    if ind["high52"] > ind["low52"]:
        pos = (p-ind["low52"]) / (ind["high52"]-ind["low52"])
        if 0.55 <= pos <= 0.92:
            s += 3
        elif pos > 0.92:
            s += 1
    return max(0, min(50, s)), reasons


def liquidity_scores(universe):
    ff = universe["FreeFloatValue"].astype(float)
    if ff.notna().sum() < 5:
        return pd.Series(7.5, index=universe.index)
    rank = ff.rank(pct=True).fillna(0.5)
    return (rank * 15).clip(0, 15)


def official_news_score(items):
    # 0..15، نقطة التعادل 7.5
    if not items:
        return 7.5
    vals = [x["sentiment"] for x in items[:12]]
    weighted = sum(v * (0.92 ** i) for i, v in enumerate(vals))
    return float(np.clip(7.5 + weighted * 1.15, 0, 15))


def web_news_score(items):
    # 0..10، نقطة التعادل 5
    if not items:
        return 5.0
    vals = [x["sentiment"] for x in items[:8]]
    weighted = sum(v * (0.9 ** i) for i, v in enumerate(vals))
    return float(np.clip(5 + weighted * 0.8, 0, 10))


@st.cache_data(ttl=900, show_spinner=False)
def download_prices(symbols):
    if not symbols:
        return {}
    ys = [s + ".KW" for s in symbols]
    try:
        data = yf.download(
            tickers=ys, period="1y", interval="1d",
            auto_adjust=False, progress=False, threads=True,
            group_by="ticker", timeout=30
        )
    except Exception:
        return {}
    out = {}
    if len(ys) == 1:
        if not data.empty:
            out[symbols[0]] = data.rename(columns=lambda x: str(x))
        return out
    for t, y in zip(symbols, ys):
        try:
            d = data[y].copy()
            if not d.dropna(how="all").empty:
                out[t] = d.dropna(how="all")
        except Exception:
            pass
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def fundamental_info(ticker):
    try:
        info = yf.Ticker(ticker + ".KW").get_info()
        return {
            "sector": info.get("sector") or "",
            "marketCap": safe_float(info.get("marketCap")),
            "trailingPE": safe_float(info.get("trailingPE")),
            "priceToBook": safe_float(info.get("priceToBook")),
            "returnOnEquity": safe_float(info.get("returnOnEquity")),
            "revenueGrowth": safe_float(info.get("revenueGrowth")),
            "earningsGrowth": safe_float(info.get("earningsGrowth")),
            "dividendYield": safe_float(info.get("dividendYield")),
        }
    except Exception:
        return {}


def fundamental_score(info):
    # 0..10، نقطة التعادل 5 عند نقص البيانات
    if not info:
        return 5.0, []
    s = 5.0
    rs = []
    pe = info.get("trailingPE", np.nan)
    pb = info.get("priceToBook", np.nan)
    roe = info.get("returnOnEquity", np.nan)
    rg = info.get("revenueGrowth", np.nan)
    eg = info.get("earningsGrowth", np.nan)
    dy = info.get("dividendYield", np.nan)
    if not np.isnan(pe):
        if 0 < pe <= 15: s += 1.2; rs.append("P/E معقول")
        elif pe > 35: s -= 0.8
    if not np.isnan(pb):
        if 0 < pb <= 2.0: s += 0.7
        elif pb > 5: s -= 0.5
    if not np.isnan(roe):
        if roe >= 0.12: s += 1.2; rs.append("ROE جيد")
        elif roe < 0: s -= 1.0
    if not np.isnan(rg):
        if rg > 0.05: s += 0.8; rs.append("نمو إيرادات")
        elif rg < -0.05: s -= 0.5
    if not np.isnan(eg):
        if eg > 0.05: s += 1.0; rs.append("نمو أرباح")
        elif eg < -0.10: s -= 0.8
    if not np.isnan(dy) and dy > 0.02:
        s += 0.6; rs.append("عائد توزيعات")
    return float(np.clip(s, 0, 10)), rs


def trade_levels(ind):
    p = ind["price"]; a = ind["atr"]
    if np.isnan(a) or a <= 0:
        a = max(p * 0.02, 0.001)
    support = max(ind["support20"], ind["ema50"] if ind["ema50"] < p else ind["support20"])
    resistance = max(ind["res20"], ind["res60"])
    entry_low = max(support, ind["ema20"] - 0.30*a)
    entry_high = min(max(p, entry_low), ind["ema20"] + 0.55*a)
    if entry_high < entry_low:
        entry_high = p
    stop = max(0.001, min(support - 0.55*a, p - 1.35*a))
    target1 = max(resistance, p + 1.6*a)
    target2 = max(target1 + 0.9*a, p + 2.5*a)
    risk_pct = ((p - stop)/p)*100 if p else np.nan
    return entry_low, entry_high, stop, target1, target2, risk_pct


def label(score):
    if score >= 82: return "شراء قوي"
    if score >= 74: return "فرصة جيدة جداً"
    if score >= 66: return "فرصة جيدة"
    if score >= 58: return "مراقبة"
    return "انتظار"


def fmt_num(x, dec=3):
    try:
        if x is None or np.isnan(float(x)): return "-"
        return f"{float(x):,.{dec}f}"
    except Exception:
        return "-"


def fmt_pct(x, multiply=False):
    try:
        if x is None or np.isnan(float(x)): return "-"
        v = float(x) * (100 if multiply else 1)
        return f"{v:.2f}%"
    except Exception:
        return "-"


st.title("📊 محلل بورصة الكويت الذكي")
st.caption("نسخة الهاتف — افتح القائمة الجانبية لاختيار السوق ثم اضغط تحليل السوق الآن")
st.caption("فحص السوق كاملًا + إفصاحات بورصة الكويت + أخبار + تحليل فني + سيولة + ترتيب أفضل فرص الشراء")

with st.sidebar:
    st.subheader("إعدادات الفحص")
    market_filter = st.selectbox("السوق", ["الكل", "Premier", "Main"], index=0)
    top_n = st.slider("عدد أفضل الفرص", 5, 30, 10, 1)
    min_score = st.slider("أقل درجة للعرض", 0, 100, 55, 1)
    web_news_mode = st.selectbox(
        "الأخبار العامة",
        ["لأفضل 30 مرشح", "السوق كامل", "إيقاف الأخبار العامة"],
        index=1
    )
    st.markdown("---")
    st.caption("الأسعار من Yahoo Finance وقد تكون مؤخرة. الإفصاحات من RSS الرسمي لبورصة الكويت.")
    run = st.button("🔄 تحليل السوق الآن", type="primary", use_container_width=True)

if "scan_result" not in st.session_state:
    st.session_state.scan_result = None
    st.session_state.details = {}

if run or st.session_state.scan_result is None:
    with st.status("جاري قراءة السوق والإفصاحات وتحليل الأسهم...", expanded=True) as status:
        universe, report_url, fallback_used = load_universe()
        st.write(f"الشركات التي تم تحميلها: {len(universe)}")
        if market_filter != "الكل":
            universe = universe[universe["Market"].str.lower() == market_filter.lower()].copy()
        universe = universe.reset_index(drop=True)
        universe["LiquidityScore"] = liquidity_scores(universe)
        rss_items = load_boursa_rss()
        st.write(f"الإفصاحات الرسمية المقروءة: {len(rss_items)}")
        price_map = download_prices(universe["Ticker"].tolist())
        st.write(f"أسهم لديها بيانات سعرية: {len(price_map)}")

        rows = []
        details = {}
        for i, r in universe.iterrows():
            tick = r["Ticker"]
            d = price_map.get(tick)
            if d is None or d.empty:
                continue
            # التعامل مع MultiIndex غير المتوقع
            if isinstance(d.columns, pd.MultiIndex):
                try:
                    d.columns = d.columns.get_level_values(-1)
                except Exception:
                    continue
            needed = {"Open","High","Low","Close","Volume"}
            if not needed.issubset(set(map(str, d.columns))):
                continue
            ind = compute_indicators(d)
            if not ind:
                continue
            ts, treasons = technical_score(ind)
            off = match_official_news(tick, r["Name"], rss_items)
            ons = official_news_score(off)
            liq = float(r["LiquidityScore"])
            base = ts + liq + ons + 5.0  # 5 نقطة محايدة للأخبار العامة مبدئيًا
            e1,e2,sl,t1,t2,risk = trade_levels(ind)
            rows.append({
                "Ticker": tick, "Name": r["Name"], "Market": r["Market"],
                "Price": ind["price"], "Technical": ts, "Liquidity": liq,
                "OfficialNews": ons, "WebNews": 5.0, "Fundamental": 5.0,
                "Score": base, "Signal": label(base),
                "RSI": ind["rsi"], "Momentum20": ind["mom20"],
                "EntryLow": e1, "EntryHigh": e2, "Stop": sl,
                "Target1": t1, "Target2": t2, "RiskPct": risk,
                "OfficialCount": len(off)
            })
            details[tick] = {"ind": ind, "price_df": d, "official": off, "web": [], "tech_reasons": treasons}

        result = pd.DataFrame(rows)
        if result.empty:
            st.error("لم أتمكن من تكوين نتائج. تحقق من اتصال الإنترنت ثم أعد المحاولة.")
            st.stop()

        # المرحلة الثانية: الأخبار العامة + الأساسيات
        pre = result.sort_values("Score", ascending=False)
        if web_news_mode == "السوق كامل":
            news_targets = pre["Ticker"].tolist()
        elif web_news_mode == "لأفضل 30 مرشح":
            news_targets = pre.head(30)["Ticker"].tolist()
        else:
            news_targets = []

        # fundamentals لأفضل 35 لتجنب الضغط على المصدر
        fund_targets = pre.head(35)["Ticker"].tolist()

        name_map = dict(zip(result["Ticker"], result["Name"]))
        web_results = {}
        if news_targets:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(google_news, name_map[t], t): t for t in news_targets}
                for fut in as_completed(futs):
                    t = futs[fut]
                    try: web_results[t] = fut.result()
                    except Exception: web_results[t] = []

        fund_results = {}
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(fundamental_info, t): t for t in fund_targets}
            for fut in as_completed(futs):
                t = futs[fut]
                try: fund_results[t] = fut.result()
                except Exception: fund_results[t] = {}

        for idx, row in result.iterrows():
            t = row["Ticker"]
            web_items = web_results.get(t, [])
            ws = web_news_score(web_items) if t in news_targets else 5.0
            fi = fund_results.get(t, {})
            fs, freasons = fundamental_score(fi)
            score = row["Technical"] + row["Liquidity"] + row["OfficialNews"] + ws + fs
            result.at[idx, "WebNews"] = ws
            result.at[idx, "Fundamental"] = fs
            result.at[idx, "Score"] = round(float(np.clip(score, 0, 100)), 1)
            result.at[idx, "Signal"] = label(result.at[idx, "Score"])
            details[t]["web"] = web_items
            details[t]["fund_info"] = fi
            details[t]["fund_reasons"] = freasons

        result = result.sort_values(["Score","Technical","Liquidity"], ascending=False).reset_index(drop=True)
        st.session_state.scan_result = result
        st.session_state.details = details
        st.session_state.report_url = report_url
        st.session_state.fallback_used = fallback_used
        status.update(label="اكتمل تحليل السوق", state="complete", expanded=False)

result = st.session_state.scan_result.copy()

c1,c2,c3,c4 = st.columns(4)
c1.metric("الأسهم المحللة", len(result))
c2.metric("شراء قوي", int((result["Score"] >= 82).sum()))
c3.metric("فرص 74+", int((result["Score"] >= 74).sum()))
c4.metric("متوسط السوق", f'{result["Score"].mean():.1f}/100')

if st.session_state.get("fallback_used"):
    st.warning("تعذر تحميل القائمة الكاملة من تقرير البورصة، لذلك تم استخدام قائمة احتياطية من السوق الأول. أعد الفحص لاحقًا لتحميل السوق كاملًا.")

st.subheader(f"🏆 أفضل {top_n} فرص حاليًا")
top = result[result["Score"] >= min_score].head(top_n).copy()
display_cols = [
    "Ticker","Name","Market","Price","Score","Signal","Technical","OfficialNews",
    "Liquidity","RSI","Momentum20","EntryLow","EntryHigh","Stop","Target1","Target2","RiskPct"
]
disp = top[display_cols].rename(columns={
    "Ticker":"الرمز","Name":"الشركة","Market":"السوق","Price":"السعر",
    "Score":"الدرجة","Signal":"التقييم","Technical":"فني/50",
    "OfficialNews":"إفصاحات/15","Liquidity":"سيولة/15","RSI":"RSI",
    "Momentum20":"زخم20%","EntryLow":"دخول من","EntryHigh":"دخول إلى",
    "Stop":"وقف مقترح","Target1":"هدف 1","Target2":"هدف 2","RiskPct":"مخاطرة %"
})
for col in ["السعر","دخول من","دخول إلى","وقف مقترح","هدف 1","هدف 2"]:
    disp[col] = disp[col].map(lambda x: round(float(x), 3) if pd.notna(x) else np.nan)
for col in ["الدرجة","فني/50","إفصاحات/15","سيولة/15","RSI","زخم20%","مخاطرة %"]:
    disp[col] = disp[col].map(lambda x: round(float(x), 1) if pd.notna(x) else np.nan)
st.dataframe(disp, use_container_width=True, hide_index=True)

st.subheader("🔎 تحليل سهم بالتفصيل")
selected = st.selectbox(
    "اختر سهمًا",
    result["Ticker"].tolist(),
    format_func=lambda t: f"{t} — {result.loc[result['Ticker']==t,'Name'].iloc[0]}"
)
row = result[result["Ticker"] == selected].iloc[0]
det = st.session_state.details.get(selected, {})
ind = det.get("ind", {})
left, mid, right = st.columns(3)
left.metric("الدرجة", f"{row['Score']:.1f}/100", row["Signal"])
mid.metric("السعر", fmt_num(row["Price"]))
right.metric("المخاطرة للستوب", fmt_pct(row["RiskPct"]))

reasons = det.get("tech_reasons", []) + det.get("fund_reasons", [])
if reasons:
    st.success("أسباب القوة: " + " • ".join(reasons[:8]))
else:
    st.info("لا توجد أسباب قوة كافية مسجلة لهذه اللحظة.")

levels = pd.DataFrame([{
    "منطقة الدخول": f"{fmt_num(row['EntryLow'])} - {fmt_num(row['EntryHigh'])}",
    "وقف مقترح": fmt_num(row["Stop"]),
    "الهدف الأول": fmt_num(row["Target1"]),
    "الهدف الثاني": fmt_num(row["Target2"]),
    "RSI": f"{row['RSI']:.1f}",
    "زخم 20 يوم": fmt_pct(row["Momentum20"]),
}])
st.dataframe(levels, use_container_width=True, hide_index=True)

price_df = det.get("price_df")
if isinstance(price_df, pd.DataFrame) and not price_df.empty:
    chart = price_df[["Close"]].tail(120).rename(columns={"Close":"السعر"})
    st.line_chart(chart)

st.markdown("### الإفصاحات الرسمية المرتبطة")
official = det.get("official", [])
if official:
    for x in official[:8]:
        sent = x["sentiment"]
        mood = "🟢" if sent > 0.5 else ("🔴" if sent < -0.5 else "⚪")
        st.markdown(f"{mood} **{re.sub('<[^>]+>',' ',x['title'])}**  \n{x['published']}")
        if x.get("link"):
            st.link_button("فتح الإفصاح", x["link"])
else:
    st.caption("لا توجد إفصاحات حديثة مطابقة في موجز RSS الحالي.")

st.markdown("### الأخبار العامة المرتبطة")
web_items = det.get("web", [])
if web_items:
    for x in web_items[:6]:
        mood = "🟢" if x["sentiment"] > 0.5 else ("🔴" if x["sentiment"] < -0.5 else "⚪")
        st.markdown(f"{mood} **{re.sub('<[^>]+>',' ',x['title'])}**  \n{x['published']}")
        if x.get("link"):
            st.link_button("فتح الخبر", x["link"])
else:
    st.caption("لم تُحمّل أخبار عامة لهذا السهم في هذا الفحص، أو لم توجد نتائج.")

fi = det.get("fund_info", {})
if fi:
    st.markdown("### لقطة أساسية")
    fdf = pd.DataFrame([{
        "القطاع": fi.get("sector","-"),
        "P/E": fmt_num(fi.get("trailingPE"),2),
        "P/B": fmt_num(fi.get("priceToBook"),2),
        "ROE": fmt_pct(fi.get("returnOnEquity"), multiply=True),
        "نمو الإيرادات": fmt_pct(fi.get("revenueGrowth"), multiply=True),
        "نمو الأرباح": fmt_pct(fi.get("earningsGrowth"), multiply=True),
        "عائد التوزيعات": fmt_pct(fi.get("dividendYield"), multiply=True),
    }])
    st.dataframe(fdf, use_container_width=True, hide_index=True)

st.markdown("---")
with st.expander("كيف تُحسب الدرجة؟"):
    st.markdown("""
- **التحليل الفني: 50 نقطة** — EMA20/50/200، RSI، MACD، زخم 20 يوم وحجم التداول.
- **السيولة: 15 نقطة** — مبنية على قيمة الأسهم الحرة من تقرير بورصة الكويت.
- **الإفصاحات الرسمية: 15 نقطة** — تحليل كلمات إيجابية/سلبية في RSS الرسمي.
- **الأخبار العامة: 10 نقاط** — أخبار ويب حديثة مرتبطة بالشركة.
- **الأساسيات: 10 نقاط** — P/E، P/B، ROE، نمو الإيرادات/الأرباح والتوزيعات عند توفرها.

البرنامج يعطي **ترتيبًا تحليليًا** وليس ضمانًا للربح أو توصية استثمارية ملزمة. الأسعار العامة قد تكون مؤخرة.
""")

st.caption("مصادر البرنامج: تقارير + RSS بورصة الكويت، وYahoo Finance للأسعار/بعض البيانات الأساسية، وGoogle News RSS للأخبار العامة.")
