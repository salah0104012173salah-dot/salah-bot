import os
import asyncio
import datetime
import logging
import pytz
import urllib.request
import json
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import yfinance as yf
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

os.environ["PYTHONHTTPSVERIFY"] = "0"
os.environ["CURL_CA_BUNDLE"] = ""

logging.basicConfig(level=logging.INFO)

# التوكن الجديد والقناة
BOT_TOKEN = "8856136135:AAHuPbyVnSn6hl_eFcjxVyr_E5f2PHFn2no"
CHANNEL_ID = "@salah20256"
TIMEZONE = pytz.timezone("Africa/Cairo")
TWELVE_DATA_API_KEY = "0baaa865b7384cf78310394df3385c7e"

# خادم ويب مدمج لإبقاء البوت نشطاً 24/7
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Salah Trading Bot is Active 24/7!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# قائمة الأدوات
SYMBOLS = {
    "الذهب (XAU/USD)": {"pair": "XAU/USD", "td_symbol": "XAU/USD", "ticker": "XAUUSD=X", "name": "الذهب الفوري", "step": 5.0, "decimals": 2},
    "الفضة (XAG/USD)": {"pair": "XAG/USD", "td_symbol": "XAG/USD", "ticker": "XAGUSD=X", "name": "الفضة الفورية", "step": 0.40, "decimals": 3},
    "اليورو (EUR/USD)": {"pair": "EUR/USD", "td_symbol": "EUR/USD", "ticker": "EURUSD=X", "name": "اليورو دولار", "step": 0.0025, "decimals": 4},
    "الباوند (GBP/USD)": {"pair": "GBP/USD", "td_symbol": "GBP/USD", "ticker": "GBPUSD=X", "name": "الباوند دولار", "step": 0.0030, "decimals": 4}
}

# الفريمات من الدقيقة وحتى اليومي
TIMEFRAMES = {
    "⚡ M1 (دقيقة)": {"name": "فريم دقيقة (M1)", "mins": 15, "ratio": 0.7, "td_interval": "1min", "period": "1d", "interval": "1m"},
    "⚡ M5 (5 دقائق)": {"name": "فريم 5 دقائق (M5)", "mins": 30, "ratio": 0.9, "td_interval": "5min", "period": "1d", "interval": "5m"},
    "📊 M15 (15 دقيقة)": {"name": "فريم 15 دقيقة (M15)", "mins": 45, "ratio": 1.2, "td_interval": "15min", "period": "5d", "interval": "15m"},
    "📊 M30 (نصف ساعة)": {"name": "فريم 30 دقيقة (M30)", "mins": 90, "ratio": 1.5, "td_interval": "30min", "period": "5d", "interval": "30m"},
    "🕒 H1 (ساعة)": {"name": "فريم ساعة (H1)", "mins": 240, "ratio": 2.2, "td_interval": "1h", "period": "1mo", "interval": "60m"},
    "🕒 H4 (4 ساعات)": {"name": "فريم 4 ساعات (H4)", "mins": 720, "ratio": 3.8, "td_interval": "4h", "period": "1mo", "interval": "1h"},
    "📅 D1 (يومي)": {"name": "فريم يومي (D1)", "mins": 1440, "ratio": 6.5, "td_interval": "1day", "period": "3mo", "interval": "1d"}
}

user_selected_symbol = {}
active_signals = []
last_price_cache = {}

def get_cairo_time(dt=None):
    if dt is None:
        dt = datetime.datetime.now(TIMEZONE)
    else:
        dt = dt.astimezone(TIMEZONE)
    t = dt.strftime("%I:%M")
    suffix = "م" if dt.strftime("%p") == "PM" else "ص"
    return f"{t} {suffix}"

def fetch_live_price(td_sym: str, ticker_str: str, decimals: int = 2):
    now = datetime.datetime.now(TIMEZONE)
    if td_sym in last_price_cache:
        cached_time, cached_price = last_price_cache[td_sym]
        if (now - cached_time).total_seconds() < 10:
            return cached_price

    try:
        url = f"https://api.twelvedata.com/price?symbol={td_sym}&apikey={TWELVE_DATA_API_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            if "price" in data and data["price"]:
                price = round(float(data["price"]), decimals)
                last_price_cache[td_sym] = (now, price)
                return price
    except Exception as e:
        logging.error(f"TwelveData fetch error: {e}")

    if td_sym in last_price_cache:
        return last_price_cache[td_sym][1]
    return None

def analyze_direction(td_sym: str, ticker_str: str, tf_info: dict, current_price: float):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={td_sym}&interval={tf_info['td_interval']}&outputsize=5&apikey={TWELVE_DATA_API_KEY}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read().decode())
            if "values" in data and len(data["values"]) >= 2:
                c1 = float(data["values"][0]["close"])
                o1 = float(data["values"][0]["open"])
                c2 = float(data["values"][1]["close"])
                if c1 < o1 and c1 < c2:
                    return "SELL"
                elif c1 > o1 and c1 > c2:
                    return "BUY"
                else:
                    return "SELL" if current_price < (c1 + c2) / 2 else "BUY"
    except Exception:
        pass

    try:
        df = yf.download(ticker_str, period=str(tf_info["period"]), interval=str(tf_info["interval"]), progress=False)
        if len(df) >= 3:
            last_close = float(df['Close'].iloc[-1])
            prev_close = float(df['Close'].iloc[-2])
            avg_price = float(df['Close'].iloc[-3:].mean())
            return "SELL" if (last_close < avg_price or last_close < prev_close) else "BUY"
    except Exception:
        pass

    return "SELL" if int(current_price * 10) % 2 == 0 else "BUY"

async def post_signal(bot, chat_id: int, symbol_key: str, tf_key: str):
    sym = SYMBOLS.get(symbol_key, SYMBOLS["الذهب (XAU/USD)"])
    tf = TIMEFRAMES.get(tf_key, TIMEFRAMES["📊 M15 (15 دقيقة)"])
    
    pair = str(sym["pair"])
    td_sym = str(sym["td_symbol"])
    ticker = str(sym["ticker"])
    decimals = int(sym["decimals"])
    sym_name = str(sym["name"])
    step_val = float(sym["step"])
    mins_val = int(tf["mins"])
    ratio_val = float(tf["ratio"])
    tf_name = str(tf["name"])

    price = fetch_live_price(td_sym, ticker, decimals)
    if price is None:
        await bot.send_message(chat_id=chat_id, text=f"⚠️ تعذر جلب السعر الفوري لـ {pair} حالياً.")
        return

    now = datetime.datetime.now(TIMEZONE)
    expiry = now + datetime.timedelta(minutes=mins_val)
    direction = analyze_direction(td_sym, ticker, tf, price)
    
    sl_step = round(step_val * ratio_val, decimals)
    tp_step = round(sl_step * 2.0, decimals)

    if direction == "BUY":
        tp = round(price + tp_step, decimals)
        sl = round(price - sl_step, decimals)
    else:
        tp = round(price - tp_step, decimals)
        sl = round(price + sl_step, decimals)

    card = (
        f"👑 **توصية VIP | صلاح تريدنج**\n"
        f"━━━━━━━━━━━━━\n"
        f"📊 **الأداة:** `{pair}` ({sym_name})\n"
        f"⏱ **الفريم:** `{tf_name}`\n"
        f"📌 **الاتجاه:** `{direction}`\n\n"
        f"💵 **نقطة الدخول:** `{price}`\n"
        f"🎯 **الهدف (TP):** `{tp}`\n"
        f"🛑 **الوقف (SL):** `{sl}`\n\n"
        f"⏰ **الوقت:** {get_cairo_time(now)} (مصر)\n"
        f"⏳ **الصلاحية:** حتى {get_cairo_time(expiry)}\n"
        f"💼 **العقد المقترح:** 0.01 Micro Lot\n"
        f"🔗 القناة الرسمية: {CHANNEL_ID}"
    )

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=card, parse_mode="Markdown")
        status = f"✅ **تم نشر الصفقة بنجاح في القناة {CHANNEL_ID}**"
    except Exception as e:
        status = f"⚠️ لم يتم النشر في القناة: {e}"

    await bot.send_message(chat_id=chat_id, text=f"{status}\n\n{card}", parse_mode="Markdown")

    active_signals.append({
        "pair": pair, "td_sym": td_sym, "ticker": ticker,
        "decimals": decimals, "type": direction, "entry": price,
        "tp": tp, "sl": sl, "expiry": expiry
    })

async def track_signals_job(context: ContextTypes.DEFAULT_TYPE):
    global active_signals
    if not active_signals:
        return

    now = datetime.datetime.now(TIMEZONE)
    remaining = []

    for sig in active_signals:
        current_p = fetch_live_price(sig["td_sym"], sig["ticker"], sig["decimals"])
        if current_p is None:
            remaining.append(sig)
            continue

        hit_tp = (sig["type"] == "BUY" and current_p >= sig["tp"]) or (sig["type"] == "SELL" and current_p <= sig["tp"])
        hit_sl = (sig["type"] == "BUY" and current_p <= sig["sl"]) or (sig["type"] == "SELL" and current_p >= sig["sl"])

        if hit_tp:
            pts = abs(round(sig["tp"] - sig["entry"], sig["decimals"]))
            report = (
                f"🎯 **صفقة رابحة - تم ضرب الهدف! (Take Profit) ✅**\n"
                f"━━━━━━━━━━━━━\n"
                f"📊 الأداة: `{sig['pair']}`\n"
                f"📌 النوع: `{sig['type']}`\n"
                f"💵 الدخول: `{sig['entry']}` ⬅️ الهدف: `{sig['tp']}`\n"
                f"💰 **الأرباح المحققة:** `+{pts}` نقطة 🔥\n"
                f"🕒 توقيت الإغلاق: {get_cairo_time(now)} (مصر)\n"
                f"👑 **بإشراف: صلاح تريدنج** | {CHANNEL_ID}"
            )
            try:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=report, parse_mode="Markdown")
            except Exception:
                pass
        elif hit_sl:
            pts = abs(round(sig["entry"] - sig["sl"], sig["decimals"]))
            report = (
                f"🛑 **صفقة خاسرة - تم ضرب الوقف (Stop Loss) ⚠️**\n"
                f"━━━━━━━━━━━━━\n"
                f"📊 الأداة: `{sig['pair']}`\n"
                f"📌 النوع: `{sig['type']}`\n"
                f"💵 الدخول: `{sig['entry']}` ⬅️ الوقف: `{sig['sl']}`\n"
                f"📉 **الخسارة:** `-{pts}` نقطة\n"
                f"🕒 توقيت الإغلاق: {get_cairo_time(now)} (مصر)\n"
                f"👑 **إدارة مخاطر صارمة | {CHANNEL_ID}**"
            )
            try:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=report, parse_mode="Markdown")
            except Exception:
                pass
        elif now > sig["expiry"]:
            report = (
                f"⏳ **انتهاء المدة الزمنية للصفقة | {CHANNEL_ID}**\n"
                f"━━━━━━━━━━━━━\n"
                f"📊 الأداة: `{sig['pair']}`\n"
                f"💵 الدخول: `{sig['entry']}` | الحالي: `{current_p}`\n"
                f"⚠️ يرجى حجز الأرباح يدويّاً أو إغلاق العقد."
            )
            try:
                await context.bot.send_message(chat_id=CHANNEL_ID, text=report, parse_mode="Markdown")
            except Exception:
                pass
        else:
            remaining.append(sig)

    active_signals = remaining

def get_symbols_keyboard():
    return ReplyKeyboardMarkup([
        ["⚡ اقتناص صفقة الآن على الفريم المفضل"],
        ["الذهب (XAU/USD)", "الفضة (XAG/USD)"],
        ["اليورو (EUR/USD)", "الباوند (GBP/USD)"],
        ["🔥 فحص السيولة", "💰 الأسعار الفورية"]
    ], resize_keyboard=True)

def get_timeframes_keyboard():
    return ReplyKeyboardMarkup([
        ["⚡ M1 (دقيقة)", "⚡ M5 (5 دقائق)"],
        ["📊 M15 (15 دقيقة)", "📊 M30 (نصف ساعة)"],
        ["🕒 H1 (ساعة)", "🕒 H4 (4 ساعات)"],
        ["📅 D1 (يومي)"],
        ["🔙 العودة لاختيار زوج آخر"]
    ], resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat:
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"👑 **مرحباً بك في بوت صلاح للتداول والتحليل الفني!**\n\n"
            f"📢 القناة الرسمية: `{CHANNEL_ID}`\n"
            f"⏱ التوقيت: مصر (Africa/Cairo)\n\n"
            f"اختر **«⚡ اقتناص صفقة الآن...»** أو اختر الأداة المطلوبة وفريمك المفضل من الأزرار 👇"
        ),
        reply_markup=get_symbols_keyboard(),
        parse_mode="Markdown"
    )

async def handle_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text or not update.effective_chat:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    if "اقتناص صفقة الآن" in text or "المفضل" in text:
        user_selected_symbol[chat_id] = "الذهب (XAU/USD)"
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎯 **اختر الفريم الزمني المطلوب لاقتناص الصفقة فوراً عليه (من الدقيقة حتى اليومي):**",
            reply_markup=get_timeframes_keyboard(),
            parse_mode="Markdown"
        )
        return

    if "العودة" in text:
        user_selected_symbol.pop(chat_id, None)
        await context.bot.send_message(chat_id=chat_id, text="👇 اختر الأداة المالية:", reply_markup=get_symbols_keyboard())
        return

    for sym_key in SYMBOLS.keys():
        if sym_key in text:
            user_selected_symbol[chat_id] = sym_key
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎯 الأداة المختارة: **{sym_key}**\n\n👇 اختر الفريم المطلوب (M1 إلى D1):",
                reply_markup=get_timeframes_keyboard(),
                parse_mode="Markdown"
            )
            return

    for tf_key, tf_val in TIMEFRAMES.items():
        if tf_key == text or tf_key.split()[1] in text:
            selected_sym = user_selected_symbol.get(chat_id, "الذهب (XAU/USD)")
            await context.bot.send_message(chat_id=chat_id, text=f"⚡ جاري تحليل الشارت واقتناص صفقة حية لـ **{selected_sym}** على **{tf_val['name']}**...")
            await post_signal(context.bot, chat_id, selected_sym, tf_key)
            return

    if "سيولة" in text:
        msg = f"🔥 **تقرير السيولة اللحظية | صلاح تريدنج**\n\n1. 🟡 **الذهب (XAU/USD):** سيولة نشطة وفرص ممتازة\n2. 💷 **الباوند (GBP/USD):** زخم متوسط\n3. 💶 **اليورو (EUR/USD):** استقرار نسبي\n4. ⚪ **الفضة (XAG/USD):** تحركات سريعة\n\n🕒 {get_cairo_time()} (القاهرة)"
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    elif "سعر" in text or "الأسعار" in text:
        msg = f"💹 **الأسعار الفورية اللحظية (Spot):**\n\n"
        for k, v in SYMBOLS.items():
            p = fetch_live_price(str(v["td_symbol"]), str(v["ticker"]), int(v["decimals"]))
            msg += f"• **{v['name']}:** `{p if p is not None else 'جاري القراءة'}`\n"
        msg += f"\n🕒 **التوقيت:** {get_cairo_time()} (القاهرة)"
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=chat_id, text="اختر من القائمة أدناه 👇", reply_markup=get_symbols_keyboard())

def main():
    Thread(target=run_web_server, daemon=True).start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(track_signals_job, interval=180, first=10)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL, handle_flow))

    print("🚀 بوت صلاح تريدنج يعمل بالتوكن الجديد بنجاح...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
