# final index.py — webhook + full menus + ultra signals
import os
import telebot
import requests
import threading
import pandas as pd
import numpy as np
from flask import Flask, request
from telebot import types
import time

# ================== CONFIG ==================
BOT_TOKEN = "7638935379:AAEmLD7JHLZ36Ywh5tvmlP1F8xzrcNrym_Q"
CHAT_ID = 1263295916
RENDER_BASE = "https://solbot.onrender.com"
WEBHOOK_URL = f"{RENDER_BASE}/{BOT_TOKEN}"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)

# ================== DATA STORAGE ==================
user_data = {}
signal_settings = {
    "RSI_BUY": 30,
    "RSI_SELL": 70,
    "EMA_PERIOD": 20,
    "MACD_FAST": 12,
    "MACD_SLOW": 26,
    "MACD_SIGNAL": 9
}
signal_threads = {}

# ================== HELPERS ==================
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr"

def get_klines(symbol, interval="15m", limit=200):
    try:
        url = f"{BINANCE_KLINES}?symbol={symbol}&interval={interval}&limit={limit}"
        data = requests.get(url, timeout=10).json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        df = pd.DataFrame(data, columns=[
            "open_time","o","h","l","c","v","close_time","qav","num_trades","taker_base","taker_quote","ignore"
        ])
        df["c"] = df["c"].astype(float)
        df["v"] = df["v"].astype(float)
        return df
    except Exception as e:
        print("get_klines error:", e)
        return None

def rsi(series, period=14):
    if len(series) < period + 1:
        return pd.Series(dtype=float)
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26, signal=9):
    if len(series) < slow + 1:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def ema(series, period=20):
    if len(series) < period:
        return pd.Series(dtype=float)
    return series.ewm(span=period, adjust=False).mean()

def technical_analysis(symbol, interval="15m"):
    df = get_klines(symbol, interval, limit=200)
    if df is None or df.empty:
        return f"❌ No data for {symbol} ({interval})"

    closes = df["c"]
    last_close = closes.iloc[-1]
    vols = df["v"]

    r = rsi(closes)
    if r.empty:
        return f"❌ RSI not available for {symbol} ({interval})"
    r_val = r.iloc[-1]

    macd_line, signal_line = macd(closes, signal_settings["MACD_FAST"], signal_settings["MACD_SLOW"], signal_settings["MACD_SIGNAL"])
    if macd_line.empty or signal_line.empty:
        return f"❌ MACD not available for {symbol} ({interval})"
    macd_val = macd_line.iloc[-1]
    macd_sig = signal_line.iloc[-1]

    e = ema(closes, signal_settings["EMA_PERIOD"])
    if e.empty:
        return f"❌ EMA not available for {symbol} ({interval})"
    e_val = e.iloc[-1]

    avg_vol = vols.mean() if len(vols)>0 else 0
    last_vol = vols.iloc[-1] if len(vols)>0 else 0

    parts = []
    # RSI interpretation
    if r_val < signal_settings["RSI_BUY"]:
        parts.append(f"RSI {r_val:.2f}: 🔵 Oversold → Buy bias")
    elif r_val > signal_settings["RSI_SELL"]:
        parts.append(f"RSI {r_val:.2f}: 🔴 Overbought → Sell bias")
    else:
        parts.append(f"RSI {r_val:.2f}: ⚪ Neutral")

    # MACD interpretation
    parts.append(f"MACD {macd_val:.6f} vs Signal {macd_sig:.6f}: " + ("🔵 Bullish" if macd_val > macd_sig else "🔴 Bearish"))

    # EMA / price
    parts.append(f"EMA{signal_settings['EMA_PERIOD']}: {e_val:.6f} — " + ("🔵 Price > EMA" if last_close > e_val else "🔴 Price < EMA"))

    # Volume signal
    parts.append(f"Volume: {last_vol:.4f} (avg {avg_vol:.4f})" + (" 🔺" if last_vol > avg_vol*1.2 else ""))

    # Composite judgement (ultra refined)
    strong_buy = (r_val < signal_settings["RSI_BUY"]) and (macd_val > macd_sig) and (last_close > e_val) and (last_vol > avg_vol)
    strong_sell = (r_val > signal_settings["RSI_SELL"]) and (macd_val < macd_sig) and (last_close < e_val) and (last_vol > avg_vol)

    judgement = "⚪ Neutral"
    if strong_buy:
        judgement = "🟢 ULTRA STRONG BUY"
    elif strong_sell:
        judgement = "🔴 ULTRA STRONG SELL"

    text = f"📊 *{symbol}* ({interval})\nPrice: {last_close:.6f}\n" + "\n".join(parts) + f"\n\n*Signal:* {judgement}"
    return text

# ================== MENU HELPERS ==================
def main_menu_markup():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("➕ Add Coin", "➖ Remove Coin")
    markup.row("📂 My Coins", "📈 Top Movers")
    markup.row("📡 Signals", "⛔ Stop Signals")
    markup.row("🔄 Reset Settings", "⚙️ Signal Settings")
    markup.row("👁 Preview Signal")
    return markup

def back_markup():
    m = types.ReplyKeyboardMarkup(resize_keyboard=True)
    m.row("⬅️ Back")
    return m

# ================== HANDLERS ==================
@bot.message_handler(commands=["start"])
def cmd_start(m):
    uid = m.chat.id
    if uid not in user_data:
        user_data[uid] = {"coins": [], "signals": {"running": False}}
    bot.send_message(uid, "🤖 Bot ready — choose:", reply_markup=main_menu_markup())

# Add coin
@bot.message_handler(func=lambda m: m.text == "➕ Add Coin")
def add_coin_prompt(m):
    bot.send_message(m.chat.id, "Send coin symbol (e.g. BTCUSDT):", reply_markup=back_markup())
    bot.register_next_step_handler(m, add_coin_process)

def add_coin_process(m):
    if m.text == "⬅️ Back":
        return cmd_start(m)
    uid = m.chat.id
    coin = m.text.strip().upper()
    if "coins" not in user_data.get(uid, {}):
        user_data.setdefault(uid, {"coins": [], "signals": {"running": False}})
    if coin not in user_data[uid]["coins"]:
        user_data[uid]["coins"].append(coin)
        bot.send_message(uid, f"✅ {coin} added.", reply_markup=main_menu_markup())
    else:
        bot.send_message(uid, f"⚠️ {coin} already in your list.", reply_markup=main_menu_markup())

# Remove coin
@bot.message_handler(func=lambda m: m.text == "➖ Remove Coin")
def remove_coin_prompt(m):
    uid = m.chat.id
    coins = user_data.get(uid, {}).get("coins", [])
    if not coins:
        bot.send_message(uid, "No coins to remove.", reply_markup=main_menu_markup()); return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in coins: markup.row(c)
    markup.row("⬅️ Back")
    bot.send_message(uid, "Select coin to remove:", reply_markup=markup)
    bot.register_next_step_handler(m, remove_coin_process)

def remove_coin_process(m):
    if m.text == "⬅️ Back":
        return cmd_start(m)
    uid = m.chat.id
    coin = m.text.strip().upper()
    if coin in user_data.get(uid, {}).get("coins", []):
        user_data[uid]["coins"].remove(coin)
        bot.send_message(uid, f"✅ {coin} removed.", reply_markup=main_menu_markup())
    else:
        bot.send_message(uid, "❌ Coin not found.", reply_markup=main_menu_markup())

# My Coins -> choose coin -> timeframe -> analysis
@bot.message_handler(func=lambda m: m.text == "📂 My Coins")
def my_coins_prompt(m):
    uid = m.chat.id
    coins = user_data.get(uid, {}).get("coins", [])
    if not coins:
        bot.send_message(uid, "No coins added yet.", reply_markup=main_menu_markup()); return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in coins: markup.row(c)
    markup.row("⬅️ Back")
    bot.send_message(uid, "Select a coin:", reply_markup=markup)
    bot.register_next_step_handler(m, my_coins_choose)

def my_coins_choose(m):
    if m.text == "⬅️ Back": return cmd_start(m)
    coin = m.text.strip().upper()
    uid = m.chat.id
    selected = coin
    # timeframe selection
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for tf in ["1m","5m","15m","1h","4h","1d"]:
        markup.row(tf)
    markup.row("⬅️ Back")
    bot.send_message(uid, f"Choose timeframe for {selected}:", reply_markup=markup)
    bot.register_next_step_handler(m, lambda mm: my_coins_show(mm, selected))

def my_coins_show(m, coin):
    if m.text == "⬅️ Back": return my_coins_prompt(m)
    tf = m.text.strip()
    res = technical_analysis(coin, tf)
    bot.send_message(m.chat.id, res, parse_mode="Markdown", reply_markup=main_menu_markup())

# Top movers (24h)
@bot.message_handler(func=lambda m: m.text == "📈 Top Movers")
def top_movers(m):
    try:
        data = requests.get(BINANCE_TICKER, timeout=10).json()
        df = pd.DataFrame(data)
        df["priceChangePercent"] = df["priceChangePercent"].astype(float)
        top = df.sort_values("priceChangePercent", ascending=False).head(10)
        text = "📈 *Top 10 Movers (24h)*\n\n"
        for _, r in top.iterrows():
            text += f"{r['symbol']}: {r['priceChangePercent']:.2f}%\n"
        bot.send_message(m.chat.id, text, parse_mode="Markdown", reply_markup=main_menu_markup())
    except Exception as e:
        bot.send_message(m.chat.id, "❌ Unable to fetch top movers.", reply_markup=main_menu_markup())

# Signals: start background per-user worker that checks their coins on 15m for strong signals
def signal_worker(uid):
    user_data.setdefault(uid, {"coins": [], "signals": {"running": False}})
    while user_data[uid]["signals"].get("running", False):
        try:
            for coin in list(user_data[uid]["coins"]):
                text = technical_analysis(coin, "15m")
                if "ULTRA STRONG BUY" in text or "ULTRA STRONG SELL" in text:
                    bot.send_message(uid, f"⚡ {coin} signal:\n{text}", parse_mode="Markdown")
        except Exception as e:
            print("signal_worker error:", e)
        time.sleep(60)

@bot.message_handler(func=lambda m: m.text == "📡 Signals")
def start_signals(m):
    uid = m.chat.id
    user_data.setdefault(uid, {"coins": [], "signals": {"running": False}})
    if user_data[uid]["signals"].get("running", False):
        bot.send_message(uid, "Signals already running.", reply_markup=main_menu_markup()); return
    user_data[uid]["signals"]["running"] = True
    t = threading.Thread(target=signal_worker, args=(uid,), daemon=True)
    signal_threads[uid] = t
    t.start()
    bot.send_message(uid, "📡 Auto signals started for your coins (15m).", reply_markup=main_menu_markup())

@bot.message_handler(func=lambda m: m.text == "⛔ Stop Signals")
def stop_signals(m):
    uid = m.chat.id
    if uid in user_data:
        user_data[uid]["signals"]["running"] = False
    bot.send_message(uid, "⛔ Auto signals stopped.", reply_markup=main_menu_markup())

# Reset settings
@bot.message_handler(func=lambda m: m.text == "🔄 Reset Settings")
def reset_settings(m):
    global signal_settings
    signal_settings = {
        "RSI_BUY": 30, "RSI_SELL": 70,
        "EMA_PERIOD": 20, "MACD_FAST": 12, "MACD_SLOW": 26, "MACD_SIGNAL": 9
    }
    bot.send_message(m.chat.id, "✅ Signal settings reset to defaults.", reply_markup=main_menu_markup())

# Signal settings interactive
@bot.message_handler(func=lambda m: m.text == "⚙️ Signal Settings")
def signal_settings_menu(m):
    text = "⚙️ Current Signal Settings:\n"
    for k, v in signal_settings.items():
        text += f"{k}: {v}\n"
    text += "\nTo update a setting send: KEY VALUE\nExample: RSI_BUY 25"
    bot.send_message(m.chat.id, text, reply_markup=back_markup())
    bot.register_next_step_handler(m, update_setting_handler)

def update_setting_handler(m):
    if m.text == "⬅️ Back":
        return cmd_start(m)
    try:
        parts = m.text.strip().split()
        if len(parts) != 2:
            raise ValueError("Bad format")
        key = parts[0].upper()
        val = int(parts[1])
        if key in signal_settings:
            signal_settings[key] = val
            bot.send_message(m.chat.id, f"✅ {key} set to {val}", reply_markup=main_menu_markup())
        else:
            bot.send_message(m.chat.id, "❌ Unknown setting key.", reply_markup=main_menu_markup())
    except Exception:
        bot.send_message(m.chat.id, "❌ Invalid input. Use: KEY VALUE", reply_markup=main_menu_markup())

# Preview signal — pick first coin or prompt to add
@bot.message_handler(func=lambda m: m.text == "👁 Preview Signal")
def preview_signal(m):
    uid = m.chat.id
    coins = user_data.get(uid, {}).get("coins", [])
    if not coins:
        bot.send_message(uid, "No coins added to preview. Add one first.", reply_markup=main_menu_markup())
        return
    # preview the first coin on 15m
    text = technical_analysis(coins[0], "15m")
    bot.send_message(uid, f"👁 Preview:\n{text}", parse_mode="Markdown", reply_markup=main_menu_markup())

# ================== WEBHOOK ROUTE ==================
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook_route():
    try:
        data = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(data)
        bot.process_new_updates([update])
    except Exception as e:
        print("webhook error:", e)
    return "ok", 200

# ================== STARTUP ==================
if __name__ == "__main__":
    # ensure we have at least one admin user_data entry (optional)
    # start webhook
    bot.remove_webhook()
    time.sleep(0.5)
    bot.set_webhook(url=WEBHOOK_URL)
    print("Webhook set to:", WEBHOOK_URL)
    # run Flask (Render provides PORT)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

