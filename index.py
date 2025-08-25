import os
import telebot
import requests
import pandas as pd
import threading
import time
import json
import numpy as np
from telebot import types
from flask import Flask

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
CHAT_ID = int(os.environ.get("CHAT_ID") or 0)
KLINES_URL = "https://api.binance.com/api/v3/klines"
TOP_COINS_URL = "https://api.binance.com/api/v3/ticker/24hr"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ================= STORAGE =================
USER_COINS_FILE = "user_coins.json"
SETTINGS_FILE = "settings.json"
LAST_SIGNAL_FILE = "last_signals.json"
MUTED_COINS_FILE = "muted_coins.json"
COIN_INTERVALS_FILE = "coin_intervals.json"

def load_json(file, default):
    if not os.path.exists(file):
        return default
    with open(file,"r") as f:
        return json.load(f)

def save_json(file,data):
    with open(file,"w") as f:
        json.dump(data,f)

coins = load_json(USER_COINS_FILE,[])
settings = load_json(SETTINGS_FILE,{"rsi_buy":20,"rsi_sell":80,"signal_validity_min":15})
last_signals = load_json(LAST_SIGNAL_FILE,{})
muted_coins = load_json(MUTED_COINS_FILE,[])
coin_intervals = load_json(COIN_INTERVALS_FILE,{})

# ================= TECHNICAL ANALYSIS =================
def get_klines(symbol, interval="15m", limit=100):
    url = f"{KLINES_URL}?symbol={symbol}&interval={interval}&limit={limit}"
    data = requests.get(url, timeout=10).json()
    closes = [float(c[4]) for c in data]
    return closes

def rsi(data, period=14):
    delta = np.diff(data)
    gain = np.maximum(delta,0)
    loss = -np.minimum(delta,0)
    avg_gain = pd.Series(gain).rolling(period).mean()
    avg_loss = pd.Series(loss).rolling(period).mean()
    rs = avg_gain/avg_loss
    return 100-(100/(1+rs))

def ema(data, period=14):
    return pd.Series(data).ewm(span=period, adjust=False).mean().tolist()

def macd(data, fast=12, slow=26, signal=9):
    fast_ema = pd.Series(data).ewm(span=fast, adjust=False).mean()
    slow_ema = pd.Series(data).ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line.tolist(), signal_line.tolist()

def generate_signal(symbol, interval):
    try:
        closes = get_klines(symbol, interval)
        if len(closes)<26: return None
        last_close = closes[-1]
        rsi_val = rsi(closes)[-1]
        macd_line, signal_line = macd(closes)
        ema_val = ema(closes,20)[-1]
        signal_msg = f"{symbol} | {interval}\nPrice: {last_close:.4f}\nRSI: {rsi_val:.2f}\nMACD: {macd_line[-1]:.4f}\nSignal Line: {signal_line[-1]:.4f}\nEMA: {ema_val:.4f}\n"
        if rsi_val < settings["rsi_buy"] and macd_line[-1] > signal_line[-1] and last_close>ema_val:
            signal_msg += "🟢 STRONG BUY"
        elif rsi_val > settings["rsi_sell"] and macd_line[-1] < signal_line[-1] and last_close<ema_val:
            signal_msg += "🔴 STRONG SELL"
        else:
            signal_msg += "⚪ Neutral"
        return signal_msg
    except Exception as e:
        print(f"[ERROR] Generating signal for {symbol}: {e}")
        return f"❌ Error fetching {symbol} data"

# ================= SIGNAL MANAGEMENT =================
auto_signals_enabled = True

def send_signal_if_new(coin, interval, sig):
    global last_signals, muted_coins
    if coin in muted_coins: return
    key = f"{coin}_{interval}"
    now_ts = time.time()
    if key not in last_signals or now_ts - last_signals[key] > settings["signal_validity_min"]*60:
        bot.send_message(CHAT_ID,f"⚡ {sig}")
        last_signals[key] = now_ts
        save_json(LAST_SIGNAL_FILE,last_signals)

def signal_scanner():
    while True:
        if auto_signals_enabled:
            active_coins = coins if coins else ["BTCUSDT","ETHUSDT","SOLUSDT"]
            for c in active_coins:
                intervals = coin_intervals.get(c, ["1m","5m","15m","1h","4h","1d"])
                for interval in intervals:
                    sig = generate_signal(c, interval)
                    if sig: send_signal_if_new(c, interval, sig)
        time.sleep(60)

threading.Thread(target=signal_scanner, daemon=True).start()

# ================= USER STATE =================
user_state = {}  # chat_id -> menu state
user_temp = {}   # temporary data storage per user

# ================= BOT MENUS =================
def main_menu(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Add Coin","📊 My Coins")
    markup.add("➖ Remove Coin","📈 Top Movers")
    markup.add("📡 Signals","🛑 Stop Signals")
    markup.add("🔄 Reset Settings","⚙️ Signal Settings","🔍 Preview Signal")
    bot.send_message(msg.chat.id,"🤖 Main Menu:", reply_markup=markup)
    user_state[msg.chat.id]=None

@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(msg.chat.id,"✅ Bot deployed and running!")
    main_menu(msg)

# ================= ADD COIN =================
@bot.message_handler(func=lambda m: m.text=="➕ Add Coin")
def add_coin_menu(msg):
    chat_id = msg.chat.id
    bot.send_message(chat_id,"Type coin symbol (e.g., BTCUSDT):")
    user_state[chat_id] = "adding_coin"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="adding_coin")
def process_add_coin(msg):
    chat_id = msg.chat.id
    coin = msg.text.upper()
    if not coin.isalnum():
        bot.send_message(chat_id,"❌ Invalid coin symbol.")
    elif coin not in coins:
        coins.append(coin)
        save_json(USER_COINS_FILE, coins)
        bot.send_message(chat_id,f"✅ {coin} added.")
    else:
        bot.send_message(chat_id,f"{coin} already exists.")
    user_state[chat_id] = None
    main_menu(msg)

# ================= REMOVE COIN =================
@bot.message_handler(func=lambda m: m.text=="➖ Remove Coin")
def remove_coin_menu(msg):
    chat_id = msg.chat.id
    if not coins:
        bot.send_message(chat_id,"⚠️ No coins saved.")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in coins:
        markup.add(c)
    markup.add("🔙 Back")
    bot.send_message(chat_id,"Select coin to remove:", reply_markup=markup)
    user_state[chat_id] = "remove_coin"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="remove_coin")
def process_remove_coin(msg):
    chat_id = msg.chat.id
    coin = msg.text.upper()
    if coin=="🔙 Back":
        main_menu(msg)
        return
    if coin in coins:
        coins.remove(coin)
        save_json(USER_COINS_FILE, coins)
        bot.send_message(chat_id,f"✅ {coin} removed.")
    else:
        bot.send_message(chat_id,"❌ Coin not found.")
    main_menu(msg)
    user_state[chat_id]=None

# ================= MY COINS =================
@bot.message_handler(func=lambda m: m.text=="📊 My Coins")
def my_coins_menu(msg):
    chat_id = msg.chat.id
    if not coins:
        bot.send_message(chat_id,"⚠️ No coins saved. Use ➕ Add Coin.")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in coins:
        markup.add(c)
    markup.add("🔙 Back")
    bot.send_message(chat_id,"Select a coin:", reply_markup=markup)
    user_state[chat_id] = "my_coins_select"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="my_coins_select")
def my_coin_selected(msg):
    chat_id = msg.chat.id
    text = msg.text
    if text=="🔙 Back":
        main_menu(msg)
        return
    if text in coins:
        user_temp[chat_id] = text
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for tf in ["1m","5m","15m","1h","1d"]:
            markup.add(tf)
        markup.add("🔙 Back")
        bot.send_message(chat_id,f"Select timeframe for {text}:", reply_markup=markup)
        user_state[chat_id] = "my_coin_tf"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="my_coin_tf")
def my_coin_timeframe(msg):
    chat_id = msg.chat.id
    tf = msg.text
    if tf=="🔙 Back":
        my_coins_menu(msg)
        return
    coin = user_temp.get(chat_id)
    sig = generate_signal(coin, tf)
    bot.send_message(chat_id, sig)

# ================= TOP MOVERS =================
@bot.message_handler(func=lambda m: m.text=="📈 Top Movers")
def top_movers_menu(msg):
    chat_id = msg.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for t in ["5m","1h","24h"]:
        markup.add(t)
    markup.add("🔙 Back")
    bot.send_message(chat_id,"Select timeframe for Top Movers:", reply_markup=markup)
    user_state[chat_id] = "top_movers"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="top_movers")
def top_movers_tf(msg):
    chat_id = msg.chat.id
    tf = msg.text
    if tf=="🔙 Back":
        main_menu(msg)
        return
    # Fetch top 10 coins by 24h percent change (placeholder)
    try:
        data = requests.get(TOP_COINS_URL, timeout=10).json()
        data.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
        top = data[:10]
        msg_text = f"Top Movers ({tf}):\n"
        for t in top:
            msg_text += f"{t['symbol']}: {t['priceChangePercent']}%\n"
        bot.send_message(chat_id, msg_text)
    except:
        bot.send_message(chat_id,"❌ Error fetching top movers")

# ================= SIGNALS =================
@bot.message_handler(func=lambda m: m.text=="📡 Signals")
def signals_menu(msg):
    chat_id = msg.chat.id
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("My Coins","All Coins","Any Coin")
    markup.add("🔙 Back")
    bot.send_message(chat_id,"Select signal type:", reply_markup=markup)
    user_state[chat_id] = "signals_select"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="signals_select")
def signals_type(msg):
    chat_id = msg.chat.id
    text = msg.text
    if text=="🔙 Back":
        main_menu(msg)
        return
    if text in ["My Coins","All Coins","Any Coin"]:
        user_temp[chat_id] = text
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for tf in ["1m","5m","15m","1h"]:
            markup.add(tf)
        markup.add("🔙 Back")
        bot.send_message(chat_id,"Select timeframe:", reply_markup=markup)
        user_state[chat_id]="signals_tf"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="signals_tf")
def signals_timeframe(msg):
    chat_id = msg.chat.id
    tf = msg.text
    if tf=="🔙 Back":
        signals_menu(msg)
        return
    signal_type = user_temp.get(chat_id)
    if signal_type=="My Coins":
        active_coins = coins
    elif signal_type=="All Coins":
        try:
            data = requests.get(TOP_COINS_URL, timeout=10).json()
            data.sort(key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
            active_coins = [t["symbol"] for t in data[:50]]
        except:
            active_coins = ["BTCUSDT","ETHUSDT"]
    elif signal_type=="Any Coin":
        bot.send_message(chat_id,"Type the coin symbol:")
        user_state[chat_id]="any_coin_input"
        user_temp[chat_id] = tf
        return
    for c in active_coins:
        sig = generate_signal(c, tf)
        bot.send_message(chat_id,sig)

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="any_coin_input")
def any_coin_input(msg):
    chat_id = msg.chat.id
    coin = msg.text.upper()
    tf = user_temp.get(chat_id)
    sig = generate_signal(coin, tf)
    bot.send_message(chat_id, sig)
    user_state[chat_id]=None
    main_menu(msg)

# ================= STOP SIGNALS =================
@bot.message_handler(func=lambda m: m.text=="🛑 Stop Signals")
def stop_signals_menu(msg):
    chat_id = msg.chat.id
    bot.send_message(chat_id,"Type coin symbol to stop signals:")
    user_state[chat_id]="stop_coin"

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="stop_coin")
def stop_coin(msg):
    chat_id = msg.chat.id
    coin = msg.text.upper()
    if coin in coins:
        muted_coins.append(coin)
        save_json(MUTED_COINS_FILE, muted_coins)
        bot.send_message(chat_id,f"🛑 Signals stopped for {coin}")
    else:
        bot.send_message(chat_id,"❌ Coin not tracked.")
    user_state[chat_id]=None
    main_menu(msg)

# ================= RESET SETTINGS =================
@bot.message_handler(func=lambda m: m.text=="🔄 Reset Settings")
def reset_settings(msg):
    global settings
    settings={"rsi_buy":20,"rsi_sell":80,"signal_validity_min":15}
    save_json(SETTINGS_FILE,settings)
    bot.send_message(msg.chat.id,
        f"✅ Settings reset to default:\nRSI Buy: {settings['rsi_buy']}\nRSI Sell: {settings['rsi_sell']}\nSignal Validity: {settings['signal_validity_min']} min"
    )
    main_menu(msg)

# ================= SIGNAL SETTINGS =================
@bot.message_handler(func=lambda m: m.text=="⚙️ Signal Settings")
def signal_settings_menu(msg):
    chat_id = msg.chat.id
    bot.send_message(chat_id,"Signal Settings feature placeholder. Implement RSI/MACD/EMA thresholds here.")
    main_menu(msg)

# ================= PREVIEW SIGNAL =================
@bot.message_handler(func=lambda m: m.text=="🔍 Preview Signal")
def preview_signal(msg):
    chat_id = msg.chat.id
    bot.send_message(chat_id,"Preview Signal placeholder. Implement preview logic here.")
    main_menu(msg)

# ================= FLASK WEBHOOK =================
@app.route("/")
def index():
    return "Bot running!",200

# ================= RUN BOT + FLASK =================
if __name__=="__main__":
    from threading import Thread
    port = int(os.environ.get("PORT", 10000))
    Thread(target=lambda: bot.infinity_polling()).start()
    app.run(host="0.0.0.0", port=port)


