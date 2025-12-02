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

# 暫存訊息
message_store = {}

# 偽裝成瀏覽器的 Header
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_id = event.message.id
    text = event.message.text.strip()
    
    # 存訊息
    message_store[msg_id] = text

    reply_text = None

    # --- 功能 A: 骰子 ---
    if text == '!骰子':
        points = random.randint(1, 6)
        reply_text = f"🎲 擲出了：{points} 點"

    # --- 功能 B: 金價 (換算1錢) ---
    elif text == '!金價':
        try:
            url = "https://rate.bot.com.tw/gold?Lang=zh-TW"
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            
            target_row = None
            for row in soup.find_all('tr'):
                if "本行賣出" in row.text and "黃金存摺" in row.text:
                    target_row = row
                    break
            
            # 取得公克價格字串 (例如 "2,850")
            price_str = "0"
            if target_row:
                price_str = target_row.select_one("td.text-right").text.strip()
            else:
                price_str = soup.select_one("td.text-right").text.strip()

            # 進行換算
            # 1. 移除逗號轉成數字
            price_per_gram = float(price_str.replace(',', ''))
            # 2. 換算成錢 (1錢 = 3.75克)
            price_per_mace = int(price_per_gram * 3.75)

            reply_text = f"💰 台灣銀行今日金價 (黃金存摺)：\n👉 1錢賣出價：NT$ {price_per_mace:,}\n(原始克價：{price_str})"

        except Exception as e:
            print(e)
            reply_text = "⚠️ 抓取金價失敗，請稍後再試。"

    # --- 功能 C: 匯率 ---
    elif text == '!匯率':
        try:
            url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
            res = requests.get(url, headers=headers)
            soup = BeautifulSoup(res.text, "html.parser")
            
            found = False
            for row in soup.find('tbody').find_all('tr'):
                if "JPY" in row.text:
                    tds = row.find_all('td')
                    sell_rate = tds[2].text.strip()
                    reply_text = f"🇯🇵 日幣 (JPY) 匯率：\n現金賣出：{sell_rate}\n(去銀行換錢的匯率)"
                    found = True
                    break
            if not found:
                reply_text = "⚠️ 找不到日幣資料。"
        except:
            reply_text = "⚠️ 抓取匯率失敗。"

    # --- 功能 D: 天氣 (預設平鎮) ---
    elif text.startswith('!天氣'):
        lat, lon = 24.9442, 121.2192
        location = "桃園平鎮"

        if "中壢" in text:
            lat, lon = 24.9653, 121.2255
            location = "桃園中壢"
        elif "楊梅" in text:
            lat, lon = 24.9084, 121.1456
            location = "桃園楊梅"
        elif "桃園" in text:
            lat, lon = 24.9936, 121.3010
            location = "桃園區"
        elif "台北" in text:
            lat, lon = 25.0330, 121.5654
            location = "台北"
        elif "台中" in text:
            lat, lon = 24.1477, 120.6736
            location = "台中"
        elif "高雄" in text:
            lat, lon = 22.6273, 120.3014
            location = "高雄"
        elif "名古屋" in text:
            lat, lon = 35.1815, 136.9066
            location = "日本名古屋"

        try:
            api_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
            res = requests.get(api_url, headers=headers).json()
            temp = res['current_weather']['temperature']
            reply_text = f"🌤 {location} 目前氣溫：{temp}°C"
        except:
            reply_text = "⚠️ 氣象資料讀取失敗。"

    if reply_text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

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
