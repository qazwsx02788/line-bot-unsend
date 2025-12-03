import os
import random
import requests
import threading
import time
from datetime import datetime
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

# 資料儲存
message_store = {}
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
os.makedirs(static_tmp_path, exist_ok=True)

# --- 核心資料結構 (以 ID 區分群組) ---
rooms_data = {}

def get_room_data(source_id):
    if source_id not in rooms_data:
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        rooms_data[source_id] = {'debt': [], 'deck': new_deck}
    return rooms_data[source_id]

# 定期清理舊圖片
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
    message_store[msg_id] = text
    reply_messages = []

    # --- 指令表 ---
    if text == '!指令':
        reply_text = (
            "🤖 機器人指令表 (群組獨立)：\n"
            "-----------------\n"
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

    # --- 記帳功能 ---
    elif text.startswith('!記 '):
        try:
            parts = text.split()
            if '欠' in parts and len(parts) >= 5:
                idx = parts.index('欠')
                d, c, amt = parts[1], parts[idx+1], int(parts[idx+2])
                note = " ".join(parts[idx+3:]) if len(parts) > idx+3 else "無備註"
                room['debt'].append({'d': d, 'c': c, 'amt': amt, 'note': note, 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"📝 [本群] 已記錄：\n{d} 欠 {c} ${amt}\n({note})"))
            else: reply_messages.append(TextSendMessage(text="⚠️ 格式：!記 @A 欠 @B 100 備註"))
        except: reply_messages.append(TextSendMessage(text="⚠️ 格式錯誤或金額非數字。"))

    elif text.startswith('!還 '):
        try:
            parts = text.split()
            if '還' in parts and len(parts) >= 5:
                d, c, amt = parts[1], parts[3], int(parts[4])
                room['debt'].append({'d': d, 'c': c, 'amt': -amt, 'note': '還款', 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"💸 [本群] 已扣除：\n{d} 還 {c} ${amt}"))
            else: reply_messages.append(TextSendMessage(text="⚠️ 格式：!還 @A 還 @B 100"))
        except: reply_messages.append(TextSendMessage(text="⚠️ 格式錯誤。"))

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
                if total > 0:
                    has_debt = True
                    res += f"🔴 {d} 欠 {c}：${total}\n"
            if not has_debt: res += "✅ 所有帳目已結清！\n"
            res += "\n🧾 【近期明細】\n"
            for r in room['debt'][-10:]:
                action = "欠" if r['amt'] > 0 else "還"
                res += f"[{r['time']}] {r['d']} {action} {r['c']} ${abs(r['amt'])}\n"
            reply_messages.append(TextSendMessage(text=res))

    elif text == '!一筆勾銷':
        room['debt'].clear()
        reply_messages.append(TextSendMessage(text="🧹 [本群] 帳本已清空！"))

    # --- 娛樂功能 ---
    elif text == '!推':
        deck = room['deck']
        if len(deck) < 2:
            reply_messages.append(TextSendMessage(text="🀄 牌底沒了！自動洗牌中..."))
            new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
            random.shuffle(new_deck)
            room['deck'] = new_deck
            deck = room['deck']
            reply_messages.append(TextSendMessage(text="✅ 洗牌完成！"))
        
        user_name = "玩家"
        try:
            if event.source.type == 'group':
                user_name = line_bot_api.get_group_member_profile(event.source.group_id, user_id).display_name
            else:
                user_name = line_bot_api.get_profile(user_id).display_name
        except: pass

        t1 = deck.pop(); t2 = deck.pop()
        score_desc = calculate_score(t1, t2)
        reply_messages.append(TextSendMessage(text=f"👤 {user_name} 的牌：\n🀄 {get_tile_text(t1)} {get_tile_text(t2)}\n📊 結果：{score_desc}\n(本群剩 {len(deck)} 張)"))

    elif text == '!洗牌':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        room['deck'] = new_deck
        reply_messages.append(TextSendMessage(text="🔄 [本群] 手動洗牌完成！"))

    elif text == '!骰子':
        reply_messages.append(TextSendMessage(text=f"🎲 擲出了：{random.randint(1, 6)} 點"))

    # --- 工具功能 ---
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
                else: reply_messages.append(TextSendMessage(text=f"⚠️ 找不到「{q}」。"))
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

# --- 處理收回 (抓兇手名字版) ---
@handler.add(UnsendEvent)
def handle_unsend(event):
    uid = event.unsend.message_id
    img_path = os.path.join(static_tmp_path, f"{uid}.jpg")
    tid = event.source.group_id if event.source.type == 'group' else event.source.user_id
    
    # 🕵️‍♂️ 抓取收回者的名字
    sender_name = "有人"
    try:
        user_id = event.source.user_id
        if event.source.type == 'group':
            profile = line_bot_api.get_group_member_profile(event.source.group_id, user_id)
            sender_name = profile.display_name
        else:
            profile = line_bot_api.get_profile(user_id)
            sender_name = profile.display_name
    except:
        pass

    if os.path.exists(img_path):
        url = f"{FQDN}/static/tmp/{uid}.jpg"
        msg = ImageSendMessage(original_content_url=url, preview_image_url=url)
        reply_text = f"抓到了！「{sender_name}」收回圖片 (如下) 👇"
        line_bot_api.push_message(tid, [TextSendMessage(text=reply_text), msg])
    elif uid in message_store:
        msg = message_store[uid]
        reply_text = f"抓到了！「{sender_name}」收回訊息：\n{msg}"
        line_bot_api.push_message(tid, TextSendMessage(text=reply_text))

if __name__ == "__main__":
    app.run()
