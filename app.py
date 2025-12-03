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

# 這是首頁，讓 UptimeRobot 敲門時看到綠燈
@app.route("/")
def home():
    return "Robot is Alive!"

# 偽裝成真人瀏覽器的身分證 (更完整的 Header)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
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

    # --- 功能 B: 金價 (強力抓取版) ---
    elif text == '!金價':
        try:
            url = "https://rate.bot.com.tw/gold?Lang=zh-TW"
            # 使用 requests.Session() 來模擬連續瀏覽
            session = requests.Session()
            res = session.get(url, headers=headers, timeout=10)
            
            # 檢查連線狀態
            if res.status_code != 200:
                print(f"連線失敗，狀態碼：{res.status_code}")
                reply_text = f"⚠️ 銀行拒絕連線 (錯誤碼 {res.status_code})，可能 IP 被擋。"
            else:
                soup = BeautifulSoup(res.text, "html.parser")
                
                # 嘗試抓取含有「本行賣出」的表格資料
                price_str = None
                
                # 方法一：精準搜尋表格
                for row in soup.find_all('tr'):
                    # 找到含有 "黃金存摺" 且含有數字的欄位
                    if "黃金存摺" in row.text:
                        # 抓取該行的所有欄位 (td)
                        tds = row.find_all('td')
                        # 通常賣出價在第 3 格 (索引 2) 或尋找靠右對齊的數字
                        for td in tds:
                            if "text-right" in td.get('class', []) and td.text.strip().replace(',','').isdigit():
                                price_str = td.text.strip()
                                break
                    if price_str: break
                
                # 方法二：如果上面失敗，暴力抓取第一個看到的價格
                if not price_str:
                     first_price = soup.select_one("td.text-right")
                     if first_price:
                         price_str = first_price.text.strip()

                if price_str:
                    # 換算
                    price_per_gram = float(price_str.replace(',', ''))
                    price_per_mace = int(price_per_gram * 3.75)
                    reply_text = f"💰 台灣銀行今日金價 (黃金存摺)：\n👉 1錢賣出價：NT$ {price_per_mace:,}\n(原始克價：{price_str})"
                else:
                    reply_text = "⚠️ 抓到了網頁但找不到價格，可能網頁改版了。"

        except Exception as e:
            print(f"金價抓取錯誤: {e}") # 這裡會把錯誤印在 Render 後台
            reply_text = "⚠️ 系統發生錯誤，請檢查後台 Log。"

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
                    # 現金賣出通常在第 3 欄 (index 2)
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
