import os
import telebot
import requests
import pandas as pd
import numpy as np
import json
import time
from flask import Flask, request
from telebot import types
import threading

# ================= CONFIG =================
BOT_TOKEN = os.environ.get("BOT_TOKEN") or "YOUR_BOT_TOKEN"
CHAT_ID = int(os.environ.get("CHAT_ID") or 0)
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or "https://yourrenderurl.com/" + BOT_TOKEN
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
    try:
        data = requests.get(url, timeout=10).json()
        if isinstance(data, dict) and "code" in data:
            return [], []
        closes = [float(c[4]) for c in data]
        volumes = [float(c[5]) for c in data]
        return closes, volumes
    except:
        return [], []

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

def ultra_signal(symbol, interval):
    closes, volumes = get_klines(symbol, interval)
    if not closes or len(closes)<26: 
        return f"❌ No data for {symbol} in {interval}"
    last_close = closes[-1]
    last_vol = volumes[-1]
    r = rsi(closes)[-1]
    m, s = macd(closes)
    e = ema(closes,20)[-1]
    signal_text = f"{symbol} | {interval}\nPrice: {last_close:.4f}\nRSI: {r:.2f}\nMACD: {m[-1]:.4f}\nSignal Line: {s[-1]:.4f}\nEMA: {e:.4f}\nVolume: {last_vol:.4f}\n"

    # Ultra refined logic
    strong_buy = r<settings["rsi_buy"] and m[-1]>s[-1] and last_close>e and last_vol>np.mean(volumes)
    strong_sell = r>settings["rsi_sell"] and m[-1]<s[-1] and last_close<e and last_vol>np.mean(volumes)

    if strong_buy:
        signal_text += "🟢 ULTRA STRONG BUY"
    elif strong_sell:
        signal_text += "🔴 ULTRA STRONG SELL"
    else:
        signal_text += "⚪ Neutral"
    return signal_text

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
                    sig = ultra_signal(c, interval)
                    if sig: send_signal_if_new(c, interval, sig)
        time.sleep(60)

threading.Thread(target=signal_scanner, daemon=True).start()

# ================= USER STATE =================
user_state = {}  # chat_id -> menu state
user_temp = {}   # temporary storage per user

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

# ---------------- ADD COIN ----------------
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

# ---------------- REMOVE COIN ----------------
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

# ---------------- MY COINS ----------------
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
        user_state[chat_id]="my_coin_tf"
    else:
        bot.send_message(chat_id,"❌ Coin not found.")

@bot.message_handler(func=lambda m: user_state.get(m.chat.id)=="my_coin_tf")
def my_coin_tf_selected(msg):
    chat_id = msg.chat.id
    text = msg.text
    coin = user_temp.get(chat_id)
    if text=="🔙 Back":
        my_coins_menu(msg)
        return
    if coin:
        analysis = ultra_signal(coin,text)
        bot.send_message(chat_id,analysis)
    else:
        bot.send_message(chat_id,"❌ Unexpected error.")

# ---------------- PLACEHOLDERS ----------------
# Top Movers, Signals, Stop Signals, Reset Settings, Signal Settings, Preview Signal
# Implement similar logic with back buttons and ultra_signal for detailed analysis
# Error handling included

# ---------------- FLASK WEBHOOK ----------------
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def webhook():
    json_str = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_str)
    bot.process_new_updates([update])
    return "!",200

@app.route("/")
def index():
    return "Bot running!",200

# ================= START WEBHOOK =================
if __name__=="__main__":
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
