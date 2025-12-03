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

# 首頁 (讓 UptimeRobot 看到綠燈)
@app.route("/")
def home():
    return "Robot is Alive!"

# 偽裝 Header (很多傳統網站需要 User-Agent 才會理你)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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

    # --- 功能 B: 金價 (改抓 999k.com.tw) ---
    elif text == '!金價':
        try:
            # 指定你給的網址
            url = "https://999k.com.tw/"
            res = requests.get(url, headers=headers, timeout=10)
            res.encoding = 'utf-8' # 強制設定編碼，避免中文字變亂碼
            soup = BeautifulSoup(res.text, "html.parser")
            
            price_str = None
            
            # 策略：在這個網站上尋找表格行 (tr)，找出含有「黃金賣出」的那一行
            for row in soup.find_all('tr'):
                # 把那一行的字全部接在一起檢查 (例如: "黃金賣出價格9400")
                row_text = row.text.strip().replace('\n', '').replace(' ', '')
                
                if "黃金賣出" in row_text:
                    # 如果找到了，就去抓這一行裡面的欄位 (td)
                    tds = row.find_all('td')
                    for td in tds:
                        # 尋找看起來像價格的數字 (移除逗號後是數字，且長度大於3)
                        val = td.text.strip().replace(',', '')
                        if val.isdigit() and len(val) >= 4:
                            price_str = val # 抓到價格了 (例如 9400)
                            break
                if price_str: break
            
            if price_str:
                # 這裡抓到的直接就是「一錢」的價格，不用再乘 3.75 了
                reply_text = f"💰 今日金價 (展寬珠寶/三井)：\n👉 1錢賣出價：NT$ {price_str}\n(資料來源：999k.com.tw)"
            else:
                # 如果首頁抓不到，有時候會藏在 gold.php 裡面，做個備用檢查
                reply_text = "⚠️ 首頁抓不到價格，可能網站改版。"

        except Exception as e:
            print(f"金價錯誤: {e}")
            reply_text = "⚠️ 抓取金價失敗，請稍後再試。"

    # --- 功能 C: 匯率 ---
    elif text == '!匯率':
        try:
            url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
            res = requests.get(url, headers=headers, timeout=10)
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
