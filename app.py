import os
import random
import requests
import threading
import time
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    ImageMessage, ImageSendMessage, UnsendEvent
)

app = Flask(__name__)

# ==========================================
# 👇 請改成你的 Render 網址 (後面不要有 /)
FQDN = "https://line-bot-unsend.onrender.com"
# ==========================================

# 設定金鑰
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# 暫存文字訊息
message_store = {}

# 建立圖片暫存資料夾
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
os.makedirs(static_tmp_path, exist_ok=True)

# 定期清理舊圖片
def cleanup_images():
    while True:
        try:
            now = time.time()
            for f in os.listdir(static_tmp_path):
                f_path = os.path.join(static_tmp_path, f)
                if os.stat(f_path).st_mtime < now - 3600:
                    os.remove(f_path)
        except:
            pass
        time.sleep(3600)

threading.Thread(target=cleanup_images, daemon=True).start()

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

# --- 處理文字訊息 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    msg_id = event.message.id
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    # 存文字訊息
    message_store[msg_id] = text

    reply_text = None

    # --- 功能 0: 指令表 ---
    if text == '!指令':
        reply_text = (
            "🤖 機器人指令表：\n"
            "-----------------\n"
            "🎮 娛樂區\n"
            "👉 !推 : 玩推筒子\n"
            "👉 !骰子 : 擲骰子\n\n"
            "🛠 工具區\n"
            "👉 !金價 : 查今日飾金賣出價\n"
            "👉 !匯率 : 查日幣匯率\n"
            "👉 !天氣 : 查平鎮氣溫\n"
            "👉 !天氣 [地名] : 查全球氣溫\n"
            "   (例: !天氣 東京、!天氣 紐約)\n"
            "-----------------"
        )

    # --- 功能 E: 多人推筒子 ---
    elif text == '!推':
        user_name = "玩家"
        try:
            if event.source.type == 'group':
                profile = line_bot_api.get_group_member_profile(event.source.group_id, user_id)
                user_name = profile.display_name
            else:
                profile = line_bot_api.get_profile(user_id)
                user_name = profile.display_name
        except:
            pass

        deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        hand = random.sample(deck, 2)
        
        def get_tile_text(v):
            return {1:"🀙",2:"🀚",3:"🀛",4:"🀜",5:"🀝",6:"🀞",7:"🀟",8:"🀠",9:"🀡",0.5:"🀆"}.get(v,"?")

        def calculate_score(t1, t2):
            if t1 == t2: return "👑 白板對子" if t1==0.5 else f"🔥 豹子 {int(t1)}對"
            pts = (t1 + t2) % 10
            return "💩 癟十" if pts==0 else f"{int(pts) if pts==int(pts) else pts} 點"

        score_desc = calculate_score(hand[0], hand[1])
        reply_text = f"👤 {user_name} 的牌：\n🀄 {get_tile_text(hand[0])} {get_tile_text(hand[1])}\n📊 結果：{score_desc}"

    # --- 功能 A: 骰子 ---
    elif text == '!骰子':
        reply_text = f"🎲 擲出了：{random.randint(1, 6)} 點"

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
                    for td in row.find_all('td'):
                        val = td.text.strip().replace(',', '')
                        if val.isdigit() and len(val) >= 4:
                            price_str = val
                            break
                if price_str: break
            
            if price_str:
                reply_text = f"💰 今日金價 (展寬珠寶/三井)：\n👉 1錢賣出價：NT$ {price_str}\n(資料來源：999k.com.tw)"
            else:
                reply_text = "⚠️ 抓不到價格，可能網站改版。"
        except:
            reply_text = "⚠️ 抓取金價失敗。"

    # --- 功能 C: 匯率 ---
    elif text == '!匯率':
        try:
            url = "https://rate.bot.com.tw/xrt?Lang=zh-TW"
            res = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            found = False
            for row in soup.find('tbody').find_all('tr'):
                if "JPY" in row.text:
                    sell_rate = row.find_all('td')[2].text.strip()
                    reply_text = f"🇯🇵 日幣 (JPY) 匯率：\n現金賣出：{sell_rate}"
                    found = True
                    break
            if not found: reply_text = "⚠️ 找不到日幣資料。"
        except:
            reply_text = "⚠️ 抓取匯率失敗。"

    # --- 功能 D: 全球天氣 (新功能) ---
    elif text.startswith('!天氣'):
        # 1. 取得使用者輸入的地點
        query_location = text.replace('!天氣', '').strip()
        
        lat, lon, location_name = None, None, None

        if not query_location:
            # 如果沒輸入地點，預設平鎮
            lat, lon, location_name = 24.9442, 121.2192, "桃園平鎮"
        else:
            # 如果有輸入，使用 Geocoding API 搜尋座標
            try:
                geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={query_location}&count=1&language=zh&format=json"
                geo_res = requests.get(geo_url, headers=headers).json()
                
                if "results" in geo_res and len(geo_res["results"]) > 0:
                    result = geo_res["results"][0]
                    lat = result["latitude"]
                    lon = result["longitude"]
                    location_name = result["name"] # 抓取 API 回傳的正式名稱
                else:
                    reply_text = f"⚠️ 找不到「{query_location}」這個地方喔！"
            except:
                reply_text = "⚠️ 地點搜尋發生錯誤。"

        # 如果成功取得了座標，就去查天氣
        if lat and lon:
            try:
                api = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
                res = requests.get(api, headers=headers).json()
                temp = res['current_weather']['temperature']
                reply_text = f"🌤 {location_name} 目前氣溫：{temp}°C"
            except:
                reply_text = "⚠️ 氣象資料讀取失敗。"

    if reply_text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# --- 處理圖片訊息 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    msg_id = event.message.id
    message_content = line_bot_api.get_message_content(msg_id)
    file_path = os.path.join(static_tmp_path, f"{msg_id}.jpg")
    with open(file_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)

# --- 處理收回事件 ---
@handler.add(UnsendEvent)
def handle_unsend(event):
    unsent_id = event.unsend.message_id
    img_path = os.path.join(static_tmp_path, f"{unsent_id}.jpg")
    
    if os.path.exists(img_path):
        img_url = f"{FQDN}/static/tmp/{unsent_id}.jpg"
        msg = ImageSendMessage(original_content_url=img_url, preview_image_url=img_url)
        reply_text = "抓到了！有人收回圖片 (如下) 👇"
        target_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
        line_bot_api.push_message(target_id, [TextSendMessage(text=reply_text), msg])
            
    elif unsent_id in message_store:
        msg = message_store[unsent_id]
        reply = f"抓到了！有人收回訊息：\n{msg}"
        target_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
        line_bot_api.push_message(target_id, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
