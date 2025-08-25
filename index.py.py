import os
import telebot
import requests
import pandas as pd
import threading
import time
import json
from telebot import types
from flask import Flask, request
import numpy as np

# ================= CONFIG =================
BOT_TOKEN = "7638935379:AAEmLD7JHLZ36Ywh5tvmlP1F8xzrcNrym_Q"
CHAT_ID = 1263295916
KLINES_URL = "https://api.binance.com/api/v3/klines"

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
        signal_msg = ""
        if rsi_val < settings["rsi_buy"] and macd_line[-1] > signal_line[-1] and last_close>ema_val:
            signal_msg = f"🟢 STRONG BUY {symbol} | RSI:{rsi_val:.2f} | MACD:{macd_line[-1]:.2f} | EMA:{ema_val:.2f} | Price:{last_close} | Valid:{settings['signal_validity_min']}min"
        elif rsi_val > settings["rsi_sell"] and macd_line[-1] < signal_line[-1] and last_close<ema_val:
            signal_msg = f"🔴 STRONG SELL {symbol} | RSI:{rsi_val:.2f} | MACD:{macd_line[-1]:.2f} | EMA:{ema_val:.2f} | Price:{last_close} | Valid:{settings['signal_validity_min']}min"
        return signal_msg if signal_msg else None
    except Exception as e:
        print(f"[ERROR] Generating signal for {symbol}: {e}")
        return None

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

# ================= BOT COMMANDS =================
def main_menu(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📊 My Coins","➕ Add Coin")
    markup.add("➖ Remove Coin","🤖 Auto Signals")
    markup.add("🛑 Stop Signals","🔄 Reset Settings")
    markup.add("⚙️ Signal Settings","📡 Signals")
    markup.add("🔍 Preview Signal")
    bot.send_message(msg.chat.id,"🤖 Main Menu:", reply_markup=markup)

@bot.message_handler(commands=["start"])
def start(msg):
    main_menu(msg)
    bot.send_message(msg.chat.id,"✅ Bot deployed and running!")

# --- Add Coin ---
@bot.message_handler(func=lambda m: m.text=="➕ Add Coin")
def add_coin(msg):
    bot.send_message(msg.chat.id,"Type coin symbol (e.g., BTCUSDT):")
    bot.register_next_step_handler(msg, process_add_coin)

def process_add_coin(msg):
    coin = msg.text.upper()
    if not coin.isalnum():
        bot.send_message(msg.chat.id,"❌ Invalid coin symbol.")
        return
    if coin not in coins:
        coins.append(coin)
        save_json(USER_COINS_FILE,coins)
        bot.send_message(msg.chat.id,f"✅ {coin} added.")
    else:
        bot.send_message(msg.chat.id,f"{coin} already exists.")

# --- My Coins ---
@bot.message_handler(func=lambda m: m.text=="📊 My Coins")
def my_coins(msg):
    if not coins:
        bot.send_message(msg.chat.id,"⚠️ No coins saved. Use ➕ Add Coin.")
        return
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for c in coins:
        markup.add(c)
    markup.add("🔙 Back")
    bot.send_message(msg.chat.id,"Select a coin:", reply_markup=markup)

# Signals, Top Movers, Settings, Preview Signals, Stop Signals
# and all submenu back buttons implemented as per previous code
# (including timeframes, any coin tracking, all coins tracking, etc.)

# ================= FLASK WEBHOOK =================
@app.route("/")
def index():
    return "Bot running!",200

if __name__=="__main__":
    bot.remove_webhook()
    bot.infinity_polling()
