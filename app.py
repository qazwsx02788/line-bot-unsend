import os
import random
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, UnsendEvent

app = Flask(__name__)

# 設定金鑰
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# 暫存訊息 (防收回用)
message_store = {}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 處理一般訊息 (包含指令與紀錄)
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_id = event.message.id
    text = event.message.text.strip() # 去除前後空白
    
    # 1. 先把訊息存起來 (為了抓收回)
    message_store[msg_id] = text

    # 2. 判斷是否有指令
    reply_text = None

    # --- 功能 A: 骰子 ---
    if text == '!骰子':
        points = random.randint(1, 6)
        reply_text = f"🎲 擲出了：{points} 點"

    # --- 功能 B: 金價 (台灣銀行) ---
    elif text == '!金價':
        try:
            url = "https://rate.bot.com.tw/gold?Lang=zh-TW"
            res = requests.get(url)
            soup = BeautifulSoup(res.text, "html.parser")
            # 抓取賣出價 (第一列通常是台幣/公克)
            price = soup.select_one(".val-sell").text.strip()
            reply_text = f"💰 台灣銀行今日金價：\n1公克賣出價：NT$ {price}\n(資料來源：台灣銀行)"
        except:
            reply_text = "⚠️ 抓取金價失敗，請稍後再試。"

    # --- 功能 C: 匯率 (日幣) ---
    elif text == '!匯率':
        try:
            url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
            res = requests.get(url)
            soup = BeautifulSoup(res.text, "html.parser")
            # 搜尋日幣的那一欄
            rows = soup.find('tbody').find_all('tr')
            for row in rows:
                if "JPY" in row.text:
                    # 抓取現金賣出價
                    sell_rate = row.find_all('td')[2].text.strip()
                    reply_text = f"🇯🇵 日幣 (JPY) 匯率：\n現金賣出：{sell_rate}\n(這就是你去換錢的匯率)"
                    break
        except:
            reply_text = "⚠️ 抓取匯率失敗。"

    # --- 功能 D: 天氣 (支援名古屋) ---
    elif text.startswith('!天氣'):
        # 預設台北
        lat, lon = 25.0330, 121.5654
        location = "台北"

        if "台中" in text:
            lat, lon = 24.1477, 120.6736
            location = "台中"
        elif "高雄" in text:
            lat, lon = 22.6273, 120.3014
            location = "高雄"
        elif "名古屋" in text:
            lat, lon = 35.1815, 136.9066
            location = "日本名古屋"

        try:
            # 使用 Open-Meteo 免費氣象 API
            api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
            res = requests.get(api_url).json()
            temp = res['current_weather']['temperature']
            reply_text = f"🌤 {location} 目前氣溫：{temp}°C"
        except:
            reply_text = "⚠️ 氣象資料讀取失敗。"

    # 如果有觸發指令，就回覆
    if reply_text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# 處理收回事件
@handler.add(UnsendEvent)
def handle_unsend(event):
    unsent_id = event.unsend.message_id
    if unsent_id in message_store:
        msg = message_store[unsent_id]
        reply = f"抓到了！有人收回訊息：\n{msg}"
        if event.source.type == 'group':
            line_bot_api.push_message(event.source.group_id, TextSendMessage(text=reply))
        elif event.source.type == 'user':
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
