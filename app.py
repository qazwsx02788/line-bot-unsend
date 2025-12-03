import os
import random
import requests
import threading
import time
import traceback
from datetime import datetime
from bs4 import BeautifulSoup
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
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
token = os.environ.get('CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('CHANNEL_SECRET')
line_bot_api = LineBotApi(token)
handler = WebhookHandler(secret)

# 資料儲存
message_store = {}
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
os.makedirs(static_tmp_path, exist_ok=True)
rooms_data = {}

def get_room_data(source_id):
    if source_id not in rooms_data:
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        rooms_data[source_id] = {
            'debt': [], 
            'deck': new_deck,
            'unsent_buffer': [] # 新增一個暫存區，專門放「忍住沒說」的收回訊息
        }
    return rooms_data[source_id]

# 定期清理
def cleanup_images():
    while True:
        try:
            now = time.time()
            for f in os.listdir(static_tmp_path):
                f_path = os.path.join(static_tmp_path, f)
                if os.stat(f_path).st_mtime < now - 3600:
                    os.remove(f_path)
        except: pass
        time.sleep(3600)

threading.Thread(target=cleanup_images, daemon=True).start()

@app.route("/")
def home(): return "Robot is Alive!"

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
    except Exception as e:
        print(f"Error: {e}")
        return 'OK'
    return 'OK'

# --- 輔助函式 ---
def get_tile_text(v):
    return {1:"🀙",2:"🀚",3:"🀛",4:"🀜",5:"🀝",6:"🀞",7:"🀟",8:"🀠",9:"🀡",0.5:"🀆"}.get(v,"?")
def calculate_score(t1, t2):
    if t1 == t2: return "👑 白板對子 (通殺!)" if t1==0.5 else f"🔥 豹子 {int(t1)}對"
    pts = (t1 + t2) % 10
    return "💩 癟十" if pts==0 else f"{int(pts) if pts==int(pts) else pts} 點"

# --- 處理文字訊息 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    msg_id = event.message.id
    text = event.message.text.strip()
    user_id = event.source.user_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    
    room = get_room_data(source_id)
    message_store[msg_id] = text # 存起來備查
    reply_messages = []

    # --- 功能 X: 抓收回 (!抓) ---
    # 這是原本要錢的功能，現在改成你問它才答，就變免費了
    if text == '!抓':
        if not room.get('unsent_buffer'):
            reply_messages.append(TextSendMessage(text="👻 目前沒有人收回訊息喔！"))
        else:
            # 把暫存區所有的收回訊息吐出來
            for item in room['unsent_buffer']:
                sender = item['sender']
                msg_type = item['type']
                content = item['content']
                
                if msg_type == 'text':
                    reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！剛剛「{sender}」收回了：\n{content}"))
                elif msg_type == 'image':
                    img_url = content
                    reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！「{sender}」剛剛收回這張圖 👇"))
                    reply_messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            
            # 吐完之後清空，避免下次重複顯示
            room['unsent_buffer'] = []

    # --- 功能 0: 指令表 ---
    elif text == '!指令':
        reply_text = (
            "🤖 機器人指令表 (免費無限版)：\n"
            "-----------------\n"
            "🕵️ 防收回 (省錢模式)\n"
            "👉 !抓 : 顯示剛剛被收回的訊息\n"
            "   (看到「xxx收回訊息」時請打這個)\n\n"
            "💰 記帳小幫手\n"
            "👉 !記 @A 欠 @B 100 [備註]\n"
            "👉 !還 @A 還 @B 100\n"
            "👉 !查帳 / !一筆勾銷\n\n"
            "🎮 娛樂區\n"
            "👉 !推 / !洗牌 / !骰子\n\n"
            "🛠 工具區\n"
            "👉 !金價 / !匯率 / !天氣\n"
            "-----------------"
        )
        reply_messages.append(TextSendMessage(text=reply_text))

    # --- 記帳 ---
    elif text.startswith('!記 '):
        try:
            parts = text.split()
            if '欠' in parts and len(parts) >= 5:
                idx = parts.index('欠')
                d, c, amt = parts[1], parts[idx+1], int(parts[idx+2])
                note = " ".join(parts[idx+3:]) if len(parts) > idx+3 else "無備註"
                room['debt'].append({'d': d, 'c': c, 'amt': amt, 'note': note, 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"📝 [本群] 已記錄：\n{d} 欠 {c} ${amt}\n({note})"))
        except: pass

    elif text.startswith('!還 '):
        try:
            parts = text.split()
            if '還' in parts and len(parts) >= 5:
                d, c, amt = parts[1], parts[3], int(parts[4])
                room['debt'].append({'d': d, 'c': c, 'amt': -amt, 'note': '還款', 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"💸 [本群] 已扣除：\n{d} 還 {c} ${amt}"))
        except: pass

    elif text == '!查帳':
        if not room['debt']:
            reply_messages.append(TextSendMessage(text="📭 [本群] 目前沒有欠款紀錄！"))
        else:
            summary = {}
            for r in room['debt']:
                k = (r['d'], r['c'])
                if k not in summary: summary[k] = 0
                summary[k] += r['amt']
            res = "📊 【本群欠款總結】\n"
            has_debt = False
            for (d, c), total in summary.items():
                if total > 0: has_debt = True; res += f"🔴 {d} 欠 {c}：${total}\n"
            if not has_debt: res += "✅ 所有帳目已結清！\n"
            reply_messages.append(TextSendMessage(text=res))

    elif text == '!一筆勾銷':
        room['debt'].clear()
        reply_messages.append(TextSendMessage(text="🧹 [本群] 帳本已清空！"))

    # --- 娛樂 ---
    elif text == '!推':
        deck = room['deck']
        if len(deck) < 2:
            new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
            random.shuffle(new_deck)
            room['deck'] = new_deck
            deck = room['deck']
            reply_messages.append(TextSendMessage(text="✅ 洗牌完成！"))
        t1 = deck.pop(); t2 = deck.pop()
        score_desc = calculate_score(t1, t2)
        reply_messages.append(TextSendMessage(text=f"🀄 結果：{score_desc}\n(剩 {len(deck)} 張)"))

    elif text == '!洗牌':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        room['deck'] = new_deck
        reply_messages.append(TextSendMessage(text="🔄 [本群] 手動洗牌完成！"))

    elif text == '!骰子':
        reply_messages.append(TextSendMessage(text=f"🎲 擲出了：{random.randint(1, 6)} 點"))

    # --- 工具 ---
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
                            price_str = val; break
                if price_str: break
            msg = f"💰 今日金價 (展寬/三井)：\n👉 1錢賣出價：NT$ {price_str}" if price_str else "⚠️ 抓不到價格。"
        except: msg = "⚠️ 抓取金價失敗。"
        reply_messages.append(TextSendMessage(text=msg))

    elif text == '!匯率':
        try:
            res = requests.get("https://rate.bot.com.tw/xrt?Lang=zh-TW", headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            found = False
            for row in soup.find('tbody').find_all('tr'):
                if "JPY" in row.text:
                    rate = row.find_all('td')[2].text.strip()
                    msg = f"🇯🇵 日幣 (JPY) 現金賣出：{rate}"; found=True; break
            if not found: msg = "⚠️ 找不到日幣資料。"
        except: msg = "⚠️ 抓取匯率失敗。"
        reply_messages.append(TextSendMessage(text=msg))

    elif text.startswith('!天氣'):
        q = text.replace('!天氣', '').strip()
        lat, lon, loc = 24.9442, 121.2192, "桃園平鎮"
        if q:
            try:
                g = requests.get(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language=zh&format=json", headers=headers).json()
                if "results" in g: lat,lon,loc = g["results"][0]["latitude"], g["results"][0]["longitude"], g["results"][0]["name"]
            except: pass
        try:
            w = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&timezone=auto", headers=headers).json()
            reply_messages.append(TextSendMessage(text=f"🌤 {loc} 目前氣溫：{w['current_weather']['temperature']}°C"))
        except:
            reply_messages.append(TextSendMessage(text="⚠️ 氣象資料失敗。"))

    if reply_messages:
        line_bot_api.reply_message(event.reply_token, reply_messages)

# --- 處理圖片 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    msg_id = event.message.id
    content = line_bot_api.get_message_content(msg_id)
    with open(os.path.join(static_tmp_path, f"{msg_id}.jpg"), 'wb') as fd:
        for chunk in content.iter_content(): fd.write(chunk)

# --- 處理收回 (被動免費版) ---
@handler.add(UnsendEvent)
def handle_unsend(event):
    uid = event.unsend.message_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    room = get_room_data(source_id)
    
    # 抓名字
    sender_name = "有人"
    try:
        user_id = event.source.user_id
        if event.source.type == 'group':
            profile = line_bot_api.get_group_member_profile(event.source.group_id, user_id)
            sender_name = profile.display_name
        else:
            profile = line_bot_api.get_profile(user_id)
            sender_name = profile.display_name
    except: pass

    # 檢查是文字還是圖片，存入暫存區 (unsent_buffer)
    img_path = os.path.join(static_tmp_path, f"{uid}.jpg")
    
    # 初始化 buffer (防呆)
    if 'unsent_buffer' not in room:
        room['unsent_buffer'] = []

    if os.path.exists(img_path):
        # 是圖片，存圖片網址
        url = f"{FQDN}/static/tmp/{uid}.jpg"
        room['unsent_buffer'].append({
            'sender': sender_name,
            'type': 'image',
            'content': url
        })
        print(f"[DEBUG] 圖片收回已暫存，等待指令觸發。")

    elif uid in message_store:
        # 是文字，存文字內容
        msg = message_store[uid]
        room['unsent_buffer'].append({
            'sender': sender_name,
            'type': 'text',
            'content': msg
        })
        print(f"[DEBUG] 文字收回已暫存: {msg}")

    # 注意！這裡我們「故意」不呼叫 push_message，所以完全不會扣錢！

if __name__ == "__main__":
    app.run()
