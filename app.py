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

# 首頁
@app.route("/")
def home():
    return "Robot is Alive!"

# 偽裝 Header
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

# --- 推筒子邏輯函數 ---
def get_tile_text(value):
    # 麻將 Unicode 對照
    tiles_map = {
        1: "🀙", 2: "🀚", 3: "🀛", 4: "🀜", 5: "🀝",
        6: "🀞", 7: "🀟", 8: "🀠", 9: "🀡", 0.5: "🀆" # 0.5 代表白板
    }
    return tiles_map.get(value, "?")

def calculate_score(t1, t2):
    # 判斷是否為豹子 (對子)
    if t1 == t2:
        # 白板對子最大 (設為 200分)，其他對子 100 + 點數
        if t1 == 0.5:
            return 200, "👑 白板對子 (最大!)"
        else:
            return 100 + t1, f"🔥 豹子 {int(t1)}對"
    
    # 計算點數 (相加取個位數)
    total = t1 + t2
    points = total % 10
    
    # 處理整數顯示
    if points == int(points):
        display_points = str(int(points))
    else:
        display_points = str(points)

    if points == 0:
        return 0, "💩 癟十 (0點)"
    else:
        return points, f"{display_points} 點"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    msg_id = event.message.id
    text = event.message.text.strip()
    
    # 存訊息
    message_store[msg_id] = text

    reply_text = None

    # --- 功能 E: 推筒子 (新功能) ---
    if text == '!推筒子':
        # 定義牌庫 (1-9筒 各4張, 白板4張)
        # 用數字表示，白板用 0.5
        deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        
        # 隨機發4張牌 (不放回抽樣，比較真實)
        hand = random.sample(deck, 4)
        
        # 分配給莊家(機器人) 和 閒家(你)
        banker_tiles = [hand[0], hand[1]]
        player_tiles = [hand[2], hand[3]]
        
        # 計算分數
        banker_score, banker_desc = calculate_score(banker_tiles[0], banker_tiles[1])
        player_score, player_desc = calculate_score(player_tiles[0], player_tiles[1])
        
        # 判斷輸贏
        result = ""
        if player_score > banker_score:
            result = "🎉 閒家贏！"
        elif player_score < banker_score:
            result = "💀 莊家贏！"
        else:
            result = "🤝 和局 (走水)"

        # 組合顯示文字
        reply_text = (
            f"🀄 【推筒子對決】\n"
            f"------------------\n"
            f"🤖 莊家：{get_tile_text(banker_tiles[0])} {get_tile_text(banker_tiles[1])}\n"
            f"📊 牌型：{banker_desc}\n"
            f"------------------\n"
            f"👤 閒家：{get_tile_text(player_tiles[0])} {get_tile_text(player_tiles[1])}\n"
            f"📊 牌型：{player_desc}\n"
            f"------------------\n"
            f"📢 結果：{result}"
        )

    # --- 功能 A: 骰子 ---
    elif text == '!骰子':
        points = random.randint(1, 6)
        reply_text = f"🎲 擲出了：{points} 點"

    # --- 功能 B: 金價 (999k.com.tw) ---
    elif text == '!金價':
        try:
            url = "https://999k.com.tw/"
            res = requests.get(url, headers=headers, timeout=10)
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, "html.parser")
            price_str = None
            for row in soup.find_all('tr'):
                row_text = row.text.strip().replace('\n', '').replace(' ', '')
                if "黃金賣出" in row_text:
                    tds = row.find_all('td')
                    for td in tds:
                        val = td.text.strip().replace(',', '')
                        if val.isdigit() and len(val) >= 4:
                            price_str = val
                            break
                if price_str: break
            
            if price_str:
                reply_text = f"💰 今日金價 (展寬珠寶/三井)：\n👉 1錢賣出價：NT$ {price_str}\n(資料來源：999k.com.tw)"
            else:
                reply_text = "⚠️ 首頁抓不到價格，可能網站改版。"
        except:
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

    # --- 功能 D: 天氣 (支援多地區) ---
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
