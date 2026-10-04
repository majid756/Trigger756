#!/usr/bin/env python3
"""Crypto Telegram bot: BTC/ZEC analysis, ZEC entry triggers, crypto news.
Modes: btc | zec | zec_watch | news
Only the Python standard library is used. Market data comes from Binance's
public data endpoint (works from GitHub's US runners).
"""
import os, sys, json, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

BASE = "https://data-api.binance.vision"
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT = os.environ["TELEGRAM_CHAT_ID"]
TEHRAN = timezone(timedelta(hours=3, minutes=30))


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def send(text):
    for i in range(0, len(text), 4000):
        data = urllib.parse.urlencode({
            "chat_id": CHAT, "text": text[i:i + 4000],
            "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage", data, timeout=25)


# ---------- indicators ----------
def klines(sym, interval, limit=300):
    d = json.loads(http_get(
        f"{BASE}/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}"))
    rows = [dict(h=float(k[2]), l=float(k[3]), c=float(k[4])) for k in d]
    return rows[:-1]  # drop the still-open candle


def ema(vals, n):
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes, n=14):
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    return 100 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(rows, n=14):
    trs = [max(r["h"] - r["l"], abs(r["h"] - p["c"]), abs(r["l"] - p["c"]))
           for p, r in zip(rows, rows[1:])]
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def analyze(sym, interval):
    rows = klines(sym, interval)
    closes = [r["c"] for r in rows]
    last, prev = rows[-1], rows[-2]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    r, a = rsi(closes), atr(rows)
    hi20 = max(x["h"] for x in rows[-21:-1])
    lo20 = min(x["l"] for x in rows[-21:-1])
    hi50 = max(x["h"] for x in rows[-50:])
    lo50 = min(x["l"] for x in rows[-50:])
    c = last["c"]
    bull = c > e50 and e20 > e50
    bear = c < e50 and e20 < e50
    sig = None
    # long: pullback to EMA20 reclaimed, or breakout above 20-candle high
    if bull and prev["l"] <= e20 * 1.003 and c > e20 and 40 <= r <= 65:
        sig = ("LONG", "پولبک به EMA20 و بازیابی آن")
    elif bull and c > hi20 and r < 75:
        sig = ("LONG", "شکست سقف ۲۰ کندل قبل")
    elif bear and prev["h"] >= e20 * 0.997 and c < e20 and 35 <= r <= 60:
        sig = ("SHORT", "پولبک به EMA20 و رد شدن از آن")
    elif bear and c < lo20 and r > 25:
        sig = ("SHORT", "شکست کف ۲۰ کندل قبل")
    plan = None
    if sig:
        d = 1 if sig[0] == "LONG" else -1
        sl = c - d * 1.5 * a
        risk = abs(c - sl)
        plan = dict(entry=c, sl=sl, tp1=c + d * 2 * risk, tp2=c + d * 3 * risk)
    trend = "صعودی" if bull else "نزولی" if bear else "خنثی/رنج"
    return dict(tf=interval, c=c, e20=e20, e50=e50, e200=e200, rsi=r, atr=a,
                hi20=hi20, lo20=lo20, hi50=hi50, lo50=lo50, trend=trend,
                sig=sig, plan=plan,
                chg7=(c / closes[-8] - 1) * 100 if len(closes) > 8 else 0)


def f(x):
    return f"{x:,.2f}" if x >= 1 else f"{x:.5f}"


def block(sym, a):
    t = f"⏱ تایم‌فریم {a['tf']}: روند {a['trend']} | RSI {a['rsi']:.0f}\n"
    t += f"EMA20 {f(a['e20'])} | EMA50 {f(a['e50'])} | EMA200 {f(a['e200'])}\n"
    t += f"حمایت‌ها: {f(a['lo20'])} / {f(a['lo50'])}\n"
    t += f"مقاومت‌ها: {f(a['hi20'])} / {f(a['hi50'])}\n"
    t += f"ATR: {f(a['atr'])}\n"
    if a["sig"]:
        p = a["plan"]
        t += (f"\n🎯 تریگر {a['sig'][0]} ({a['sig'][1]})\n"
              f"ورود ≈ {f(p['entry'])} | حد ضرر {f(p['sl'])}\n"
              f"TP1 {f(p['tp1'])} | TP2 {f(p['tp2'])}\n")
    else:
        t += "\n🎯 تریگر ورود معتبر فعلاً وجود ندارد.\n"
    return t


DISC = ("\n⚠️ تحلیل قاعده‌محور خودکار (نه توصیه مالی). سایز پوزیشن و حد ضرر "
        "با مدیریت ریسک خودت. قیمت‌ها اسپات بایننس‌اند؛ فیوچرز کمی تفاوت دارد.")


def report(sym, title):
    d1, h4 = analyze(sym, "1d"), analyze(sym, "4h")
    now = datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M")
    t = f"📊 {title} — {now} تهران\nقیمت: {f(h4['c'])} | تغییر ۷ روزه: {d1['chg7']:+.1f}%\n\n"
    t += block(sym, d1) + "\n" + block(sym, h4)
    return t + DISC


# ---------- news ----------
FEEDS = ["https://www.coindesk.com/arc/outboundfeeds/rss/",
         "https://cointelegraph.com/rss", "https://decrypt.co/feed"]
KEYS = ["bitcoin", "btc", "ethereum", "etf", "sec ", "fed", "rate", "inflation",
        "tariff", "regulat", "hack", "exploit", "liquidat", "zcash", "zec",
        "binance", "bybit", "okx", "tether", "stablecoin", "ban", "sanction",
        "iran", "war", "treasury", "cpi", "trump"]


def news(hours=3):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen, items = set(), []
    for url in FEEDS:
        try:
            root = ET.fromstring(http_get(url))
        except Exception:
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            try:
                ts = parsedate_to_datetime(it.findtext("pubDate"))
            except Exception:
                continue
            if ts < since or title.lower() in seen:
                continue
            if any(k in title.lower() for k in KEYS):
                seen.add(title.lower())
                items.append((ts, title, link))
    items.sort(reverse=True)
    return items[:8]


def main(mode):
    if mode == "btc":
        send(report("BTCUSDT", "تحلیل روزانه بیت‌کوین (BTC)"))
    elif mode == "zec":
        send(report("ZECUSDT", "تحلیل روزانه زیکش (ZEC)"))
    elif mode == "zec_watch":
        a = analyze("ZECUSDT", "4h")
        if a["sig"]:
            send("🔔 هشدار تریگر ZEC\nقیمت: " + f(a["c"]) + "\n\n" + block("ZECUSDT", a) + DISC)
    elif mode == "news":
        items = news()
        if items:
            t = "📰 اخبار مهم کریپتو (۳ ساعت اخیر)\n\n"
            t += "\n\n".join(f"• {ti}\n{li}" for _, ti, li in items)
            send(t)
    else:
        raise SystemExit("mode must be btc|zec|zec_watch|news")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "btc")
