import os
import random
import requests
import threading
import time
from bs4 import BeautifulSoup
from flask import Flask, request, abort, send_from_directory
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    ImageMessage, ImageSendMessage, UnsendEvent
)

app = Flask(__name__)

# ==========================================
# 👇 請把這裡改成你的 Render 網址 (後面不要有 /)
# 例如: "https://line-bot-unsend.onrender.com"
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

# 定期清理舊圖片 (避免硬碟爆掉) - 每 1 小時執行一次
def cleanup_images():
    while True:
        try:
            now = time.time()
            for f in os.listdir(static_tmp_path):
                f_path = os.path.join(static_tmp_path, f)
                # 如果檔案超過 1 小時就刪除
                if os.stat(f_path).st_mtime < now - 3600:
                    os.remove(f_path)
        except:
            pass
        time.sleep(3600)

# 啟動清理執行緒
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

    # --- 功能 E: 多人推筒子 (輸入 !推) ---
    if text == '!推':
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

    # --- 功能 D: 天氣 ---
    elif text.startswith('!天氣'):
        lat, lon = 24.9442, 121.2192
        location = "桃園平鎮"
        if "中壢" in text: lat, lon, location = 24.9653, 121.2255, "桃園中壢"
        elif "楊梅" in text: lat, lon, location = 24.9084, 121.1456, "桃園楊梅"
        elif "桃園" in text: lat, lon, location = 24.9936, 121.3010, "桃園區"
        elif "台北" in text: lat, lon, location = 25.0330, 121.5654, "台北"
        elif "台中" in text: lat, lon, location = 24.1477, 120.6736, "台中"
        elif "高雄" in text: lat, lon, location = 22.6273, 120.3014, "高雄"
        elif "名古屋" in text: lat, lon, location = 35.1815, 136.9066, "日本名古屋"

        try:
            api = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto"
            res = requests.get(api, headers=headers).json()
            reply_text = f"🌤 {location} 目前氣溫：{res['current_weather']['temperature']}°C"
        except:
            reply_text = "⚠️ 氣象資料失敗。"

    if reply_text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

# --- 處理圖片訊息 (儲存圖片) ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    msg_id = event.message.id
    # 下載圖片內容
    message_content = line_bot_api.get_message_content(msg_id)
    # 存檔路徑
    file_path = os.path.join(static_tmp_path, f"{msg_id}.jpg")
    
    with open(file_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)

# --- 處理收回事件 (文字+圖片) ---
@handler.add(UnsendEvent)
def handle_unsend(event):
    unsent_id = event.unsend.message_id
    
    # 1. 檢查是不是圖片收回
    img_path = os.path.join(static_tmp_path, f"{unsent_id}.jpg")
    
    if os.path.exists(img_path):
        # 圖片存在，發送圖片
        img_url = f"{FQDN}/static/tmp/{unsent_id}.jpg"
        msg = ImageSendMessage(original_content_url=img_url, preview_image_url=img_url)
        reply_text = "抓到了！有人收回圖片 (如下) 👇"
        
        # 先傳提示文字，再傳圖片
        if event.source.type == 'group':
            line_bot_api.push_message(event.source.group_id, [TextSendMessage(text=reply_text), msg])
        elif event.source.type == 'user':
            line_bot_api.push_message(event.source.user_id, [TextSendMessage(text=reply_text), msg])
            
    # 2. 檢查是不是文字收回
    elif unsent_id in message_store:
        msg = message_store[unsent_id]
        reply = f"抓到了！有人收回訊息：\n{msg}"
        if event.source.type == 'group':
            line_bot_api.push_message(event.source.group_id, TextSendMessage(text=reply))
        elif event.source.type == 'user':
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply))

if __name__ == "__main__":
    app.run()
