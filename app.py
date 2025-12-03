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
# 👇 請改成你的 Render 網址 (開頭 https, 後面不要有 /)
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

# 取得房間資料 (群組獨立化)
def get_room_data(source_id):
    if source_id not in rooms_data:
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        rooms_data[source_id] = {
            'debt': [], 
            'deck': new_deck,
            'unsent_buffer': [],
            # 賭局狀態
            'game': {
                'banker_id': None,
                'banker_name': None,
                'banker_card_val': None, # 莊家點數
                'banker_desc': "",       # 莊家牌型文字
                'bets': {},              # 下注池 (保留至改動)
                'player_results': {},    # 本局閒家暫存點數
                'session_log': [],       # 大局流水帳
                'played_users': []       # 本小局已開牌名單
            }
        }
    return rooms_data[source_id]

# 定期清理圖片
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

# --- 推筒子邏輯 ---
def get_tile_text(v):
    tiles_map = {
        1: "🀙", 2: "🀚", 3: "🀛", 4: "🀜", 5: "🀝",
        6: "🀞", 7: "🀟", 8: "🀠", 9: "🀡", 0.5: "🀆"
    }
    return tiles_map.get(v, "🀫")

def calculate_score(t1, t2):
    if t1 == t2: return "👑 白板對子" if t1==0.5 else f"🔥 豹子 {int(t1)}對"
    pts = (t1 + t2) % 10
    return "💩 癟十" if pts==0 else f"{int(pts) if pts==int(pts) else pts} 點"

def get_score_value(t1, t2):
    if t1 == t2: return 1000 if t1 == 0.5 else 100 + t1
    score = (t1 + t2) % 10
    return 0 if score == 0 else score

# 抓取使用者名稱
def get_user_name(event, user_id=None):
    if not user_id: user_id = event.source.user_id
    try:
        if event.source.type == 'group':
            profile = line_bot_api.get_group_member_profile(event.source.group_id, user_id)
            return profile.display_name
        else:
            profile = line_bot_api.get_profile(user_id)
            return profile.display_name
    except:
        return "玩家"

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

    # --- 功能 0: 指令表 ---
    if text == '!指令':
        reply_text = (
            "🤖 機器人全功能指令：\n"
            "-----------------\n"
            "🎰 無縫流水局 (自動記帳)\n"
            "1. 👉 !搶莊 : 開新大局\n"
            "2. 👉 !下注 200 : 設定金額 (自動延用)\n"
            "3. 👉 !推 : 發牌 (全員開完秒結算)\n"
            "   ⚠️ 單局重複推 = 罰款$100\n"
            "4. 👉 !收牌 : 強制結算本局 (沒開判輸)\n"
            "5. 👉 !下莊 : 結束大局，寫入公帳\n\n"
            "💰 記帳區\n"
            "👉 !記 / !還 / !查帳 / !一筆勾銷\n\n"
            "🕵️ 防收回\n"
            "👉 !抓 : 抓收回訊息\n"
            "㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️\n"
            "-----------------"
        )
        reply_messages.append(TextSendMessage(text=reply_text))

    # --- 🎰 大局控制 ---
    elif text == '!搶莊':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        room['deck'] = new_deck
        
        banker_name = get_user_name(event)
        room['game'] = {
            'banker_id': user_id,
            'banker_name': banker_name,
            'banker_card_val': None,
            'banker_desc': "",
            'bets': {},
            'player_results': {},
            'session_log': [],
            'played_users': []
        }
        reply_messages.append(TextSendMessage(text=f"👑 新局開始！莊家：{banker_name}\n🀄 牌已洗好 (40張)\n👉 閒家請「!下注」，所有人都「!推」完會自動下一局"))

    elif text == '!下莊':
        game = room['game']
        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 無莊家。"))
        else:
            if not game['session_log']:
                reply_messages.append(TextSendMessage(text="⚠️ 本次大局沒有輸贏紀錄。"))
            else:
                count = 0
                summary_text = "🧾 【大局總結算】\n"
                for r in game['session_log']:
                    room['debt'].append(r)
                    count += 1
                    summary_text += f"▪ {r['d']} {r['note']} {r['c']} ${r['amt']}\n"
                
                game['banker_id'] = None
                game['session_log'] = []
                game['bets'] = {}
                
                reply_messages.append(TextSendMessage(text=f"{summary_text}\n✅ 已寫入公帳！莊家下台。"))

    # --- 🃏 下注 ---
    elif text.startswith('!下注'):
        game = room['game']
        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 沒人做莊！請先「!搶莊」"))
        elif user_id == game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 莊家不用下注"))
        elif user_id in game['played_users']:
            reply_messages.append(TextSendMessage(text="⚠️ 本局已推牌，下局生效"))
        else:
            try:
                parts = text.split()
                amount = 100
                if len(parts) > 1 and parts[1].isdigit():
                    amount = int(parts[1])
                
                player_name = get_user_name(event)
                game['bets'][user_id] = {'amount': amount, 'name': player_name}
                reply_messages.append(TextSendMessage(text=f"💰 {player_name} 下注 ${amount} (之後自動延用)"))
            except: pass

    # --- 🃏 強制收牌 ---
    elif text == '!收牌':
        game = room['game']
        deck = room['deck']
        
        if not game['banker_id']: return
        
        # 抓沒開牌的判輸
        missing_text = ""
        timestamp = datetime.now().strftime("%H:%M")
        
        for pid, info in game['bets'].items():
            if pid not in game['played_users']:
                amt = info['amount']
                p_name = info['name']
                missing_text += f"💤 {p_name} 沒開牌 ❌ 輸 ${amt}\n"
                game['session_log'].append({'d': p_name, 'c': game['banker_name'], 'amt': amt, 'note': '未開牌判輸', 'time': timestamp})

        # 洗牌檢查
        shuffle_msg = ""
        needed = (len(game['bets']) + 1) * 2
        if len(deck) < needed:
            new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(new_deck); room['deck'] = new_deck
            shuffle_msg = "\n🀄 牌底不足，已自動洗牌！"

        # 重置小局
        game['played_users'] = []
        game['player_results'] = {}
        game['banker_card_val'] = None
        game['banker_desc'] = ""
        
        reply_msg = f"🔄 強制結算！{shuffle_msg}\n"
        if missing_text: reply_msg += f"----------------\n{missing_text}"
        reply_msg += "👉 下一局開始，請直接「!推」"
        
        reply_messages.append(TextSendMessage(text=reply_msg))

    # --- 核心：自動結算推牌 (!推) ---
    elif text == '!推':
        game = room['game']
        deck = room['deck']
        user_name = get_user_name(event)

        if not game['banker_id']:
            # 路人模式
            if len(deck) < 2:
                deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(deck); room['deck']=deck
            t1, t2 = deck.pop(), deck.pop()
            reply_messages.append(TextSendMessage(text=f"👤 {user_name}：{get_tile_text(t1)} {get_tile_text(t2)} ({calculate_score(t1, t2)})"))
            
        else:
            # 1. 檢查是否重複推 (罰款邏輯)
            if user_id in game['played_users']:
                log = {'d': user_name, 'c': '公桶', 'amt': 100, 'note': '手賤罰款', 'time': datetime.now().strftime("%H:%M")}
                game['session_log'].append(log)
                reply_messages.append(TextSendMessage(text=f"😡 {user_name} 重複推牌！罰 $100"))
            
            # 2. 檢查資格
            elif user_id != game['banker_id'] and user_id not in game['bets']:
                reply_messages.append(TextSendMessage(text=f"⚠️ {user_name} 沒下注不能玩！"))
                
            else:
                # 3. 發牌
                if len(deck) < 2:
                    new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(new_deck); room['deck'] = new_deck
                    deck = room['deck']
                    reply_messages.append(TextSendMessage(text="🀄 自動洗牌完成！"))

                t1, t2 = deck.pop(), deck.pop()
                val = get_score_value(t1, t2)
                desc = calculate_score(t1, t2)
                
                game['played_users'].append(user_id)
                output_msg = ""

                if user_id == game['banker_id']:
                    game['banker_card_val'] = val
                    game['banker_desc'] = f"{get_tile_text(t1)} {get_tile_text(t2)} ({desc})"
                    output_msg = f"👑 莊家 {user_name} 開牌：\n{game['banker_desc']}\n"
                else:
                    output_msg = f"👤 {user_name} 開牌：\n{get_tile_text(t1)} {get_tile_text(t2)} ({desc})\n"
                    game['player_results'][user_id] = {'val': val, 'name': user_name}

                # --- 🔥 自動結算判定 ---
                # 條件：莊家已開牌 AND 所有下注的閒家都已開牌
                all_bets_users = set(game['bets'].keys())
                all_played_users = set(game['played_users'])
                
                if game['banker_card_val'] is not None and all_bets_users.issubset(all_played_users):
                    
                    output_msg += "\n⚔️ 所有人到齊！本局結算：\n"
                    b_val = game['banker_card_val']
                    b_name = game['banker_name']
                    timestamp = datetime.now().strftime("%H:%M")

                    for pid in game['bets']:
                        p_res = game['player_results'].get(pid)
                        if not p_res: continue
                        
                        p_val = p_res['val']
                        p_name = p_res['name']
                        amt = game['bets'][pid]['amount']

                        if p_val > b_val:
                            output_msg += f"✅ {p_name} 贏 ${amt}\n"
                            game['session_log'].append({'d': b_name, 'c': p_name, 'amt': amt, 'note': '推筒', 'time': timestamp})
                        elif p_val < b_val:
                            output_msg += f"❌ {p_name} 輸 ${amt}\n"
                            game['session_log'].append({'d': p_name, 'c': b_name, 'amt': amt, 'note': '推筒', 'time': timestamp})
                        else:
                            output_msg += f"🤝 {p_name} 走水\n"

                    output_msg += "\n🔄 自動開始下一局！(金額照舊)"
                    
                    # 重置小局
                    game['played_users'] = []
                    game['player_results'] = {}
                    game['banker_card_val'] = None
                    game['banker_desc'] = ""

                elif game['banker_card_val'] is None:
                    output_msg += "(等待莊家開牌...)"
                else:
                    remaining = len(game['bets']) - len(game['player_results'])
                    output_msg += f"(還有 {remaining} 位閒家未開...)"

                reply_messages.append(TextSendMessage(text=output_msg))

    # --- 記帳區 ---
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
            res += "\n🧾 【近期明細】\n"
            for r in room['debt'][-10:]:
                action = "欠" if r['amt'] > 0 else "還"
                res += f"[{r['time']}] {r['d']} {action} {r['c']} ${abs(r['amt'])}\n"
            reply_messages.append(TextSendMessage(text=res))

    elif text == '!一筆勾銷':
        room['debt'].clear()
        reply_messages.append(TextSendMessage(text="🧹 [本群] 帳本已清空！"))

    # --- 工具區 ---
    elif text == '!抓':
        if not room.get('unsent_buffer'):
            reply_messages.append(TextSendMessage(text="👻 目前沒有人收回訊息喔！"))
        else:
            for item in room['unsent_buffer']:
                sender = item['sender']
                msg_type = item['type']
                content = item['content']
                if msg_type == 'text':
                    reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！「{sender}」收回：\n{content}"))
                elif msg_type == 'image':
                    img_url = content
                    reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！「{sender}」收回圖片 👇"))
                    reply_messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            room['unsent_buffer'] = []

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
        except: pass

    if reply_messages:
        line_bot_api.reply_message(event.reply_token, reply_messages)

# --- 處理圖片 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    msg_id = event.message.id
    content = line_bot_api.get_message_content(msg_id)
    with open(os.path.join(static_tmp_path, f"{msg_id}.jpg"), 'wb') as fd:
        for chunk in content.iter_content(): fd.write(chunk)

# --- 處理收回 (被動) ---
@handler.add(UnsendEvent)
def handle_unsend(event):
    uid = event.unsend.message_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    room = get_room_data(source_id)
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

    img_path = os.path.join(static_tmp_path, f"{uid}.jpg")
    if 'unsent_buffer' not in room: room['unsent_buffer'] = []

    if os.path.exists(img_path):
        url = f"{FQDN}/static/tmp/{uid}.jpg"
        room['unsent_buffer'].append({'sender': sender_name, 'type': 'image', 'content': url})
    elif uid in message_store:
        msg = message_store[uid]
        room['unsent_buffer'].append({'sender': sender_name, 'type': 'text', 'content': msg})

if __name__ == "__main__":
    app.run()
