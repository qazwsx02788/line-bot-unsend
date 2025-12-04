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
    ImageMessage, ImageSendMessage, UnsendEvent, JoinEvent
)
from googletrans import Translator

app = Flask(__name__)

# ==========================================
# 👇 1. 請改成你的 Render 網址 (開頭 https, 後面不要有 /)
FQDN = "https://line-bot-unsend.onrender.com"

# 👇 2. 請填入「你的」User ID (最高權限)
# (如果不確定，部署後對機器人輸入 !id 查詢)
OWNER_ID = "U6d111042c6ecb593b8c6bb781417c45f"
# ==========================================

# 設定金鑰
token = os.environ.get('CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('CHANNEL_SECRET')
line_bot_api = LineBotApi(token)
handler = WebhookHandler(secret)

translator = Translator()
message_store = {}
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
os.makedirs(static_tmp_path, exist_ok=True)
rooms_data = {}

# --- 權限管理 ---
ADMINS = {OWNER_ID} 
AUTHORIZED_GROUPS = set()

def get_room_data(source_id):
    if source_id not in rooms_data:
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        rooms_data[source_id] = {
            'debt': [], 'deck': new_deck, 'unsent_buffer': [],
            'game': {
                'banker_id': None, 'banker_name': None, 'game_type': None,
                'banker_card_val': None, 'banker_desc': "", 'bets': {},
                'player_results': {}, 'session_log': [], 'played_users': []
            }
        }
    return rooms_data[source_id]

def cleanup_images():
    while True:
        try:
            now = time.time()
            for f in os.listdir(static_tmp_path):
                f_path = os.path.join(static_tmp_path, f)
                if os.stat(f_path).st_mtime < now - 3600: os.remove(f_path)
        except: pass
        time.sleep(3600)

threading.Thread(target=cleanup_images, daemon=True).start()

@app.route("/")
def home(): return "Robot is Alive!"

headers = {"User-Agent": "Mozilla/5.0"}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    except Exception as e: print(f"Error: {e}"); return 'OK'
    return 'OK'

# --- 邏輯區 ---
def get_tile_text(v):
    tiles_map = {1:"🀙",2:"🀚",3:"🀛",4:"🀜",5:"🀝",6:"🀞",7:"🀟",8:"🀠",9:"🀡",0.5:"🀆"}
    return tiles_map.get(v, "🀫")
def calc_tui_score(t1, t2):
    if t1 == t2: return "👑白板對" if t1==0.5 else f"🔥{int(t1)}對"
    pts = (t1 + t2) % 10
    return "💩癟十" if pts==0 else f"{int(pts) if pts==int(pts) else pts}點"
def get_tui_value(t1, t2):
    if t1 == t2: return 1000 if t1 == 0.5 else 100 + t1
    score = (t1 + t2) % 10
    return 0 if score == 0 else score
def get_poker_text(card):
    rank, suit = card
    r_text = {1:'A', 11:'J', 12:'Q', 13:'K'}.get(rank, str(rank))
    return f"{suit}{r_text}"
def calc_niu_score(hand):
    values = [10 if r >= 10 else r for r, s in hand]
    total = sum(values); niu_point = -1 
    for i in range(5):
        for j in range(i+1, 5):
            rem = values[i] + values[j]
            if (total - rem) % 10 == 0:
                np = rem % 10; np = 10 if np==0 else np
                if np > niu_point: niu_point = np
    if niu_point == -1: return 0, "💩 無牛", 1
    elif niu_point == 10: return 100, "🎉 牛牛", 3
    else: return niu_point * 10, f"🐂 牛{niu_point}", 2 if niu_point >= 8 else 1

def get_user_name(event, user_id=None):
    if not user_id: user_id = event.source.user_id
    try:
        if event.source.type == 'group': return line_bot_api.get_group_member_profile(event.source.group_id, user_id).display_name
        else: return line_bot_api.get_profile(user_id).display_name
    except: return "玩家"

def check_auth_and_leave(group_id):
    time.sleep(20)
    if group_id not in AUTHORIZED_GROUPS:
        try:
            line_bot_api.push_message(group_id, TextSendMessage(text="⏳ 驗證超時！請付費購買授權。\n👋 機器人自動退出..."))
            line_bot_api.leave_group(group_id)
        except: pass

@handler.add(JoinEvent)
def handle_join(event):
    gid = event.source.group_id
    if gid in AUTHORIZED_GROUPS:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 授權成功！機器人已啟動。"))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 【未授權警告】\n此群組尚未開通。\n請管理員在 20 秒內輸入「!開通」\n否則機器人將自動退出！"))
        threading.Thread(target=check_auth_and_leave, args=(gid,), daemon=True).start()

# --- 訊息處理 ---
@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    msg_id = event.message.id
    text = event.message.text.strip()
    user_id = event.source.user_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    
    room = get_room_data(source_id)
    message_store[msg_id] = text
    reply_messages = []

    # --- 權限指令 ---
    if text == '!id':
        reply_messages.append(TextSendMessage(text=f"User ID:\n{user_id}"))
    elif text == '!開通':
        if user_id in ADMINS or user_id == OWNER_ID:
            AUTHORIZED_GROUPS.add(source_id)
            reply_messages.append(TextSendMessage(text="✅ 授權成功！本群組已開通。"))
        else:
            reply_messages.append(TextSendMessage(text="🚫 權限不足！"))
    elif text.startswith('!新增管理員 '):
        if user_id == OWNER_ID:
            new_admin = text.replace('!新增管理員', '').strip()
            if new_admin: ADMINS.add(new_admin); reply_messages.append(TextSendMessage(text=f"👮‍♂️ 已新增管理員。"))

    # --- 翻譯 ---
    elif text.startswith('!泰 '):
        c = text[3:].strip()
        if c:
            try: reply_messages.append(TextSendMessage(text=f"🇹🇭 泰文：\n{translator.translate(c, dest='th').text}"))
            except: reply_messages.append(TextSendMessage(text="⚠️ 翻譯失敗"))
    elif not text.startswith('!'):
        try:
            detected = translator.detect(text)
            if detected.lang == 'th': # 移除信心度門檻，強制翻譯
                res = translator.translate(text, src='th', dest='zh-tw')
                if res.text != text: reply_messages.append(TextSendMessage(text=f"🇹🇭 泰翻中：\n{res.text}"))
        except: pass

    # --- 指令表 ---
    if text == '!指令':
        reply_text = (
            "🤖 機器人指令表：\n"
            "-----------------\n"
            "🔒 授權\n"
            "👉 !id / !開通 (限管)\n\n"
            "🎰 流水局\n"
            "1. 👉 !搶莊\n"
            "2. 👉 !下注 200\n"
            "3. 👉 !推 (推筒/妞妞)\n"
            "4. 👉 !收牌\n"
            "5. 👉 !下莊 (亂喊罰一萬)\n\n"
            "🇹🇭 翻譯\n"
            "👉 !泰 [文] / 傳泰文自動翻\n\n"
            "💰 記帳 & 工具\n"
            "👉 !記 / !還 / !查帳 / !一筆勾銷\n"
            "👉 !抓 (防收回)\n"
            "👉 !金價 / !匯率 / !天氣\n"
            "-----------------\n"
            "㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
        )
        reply_messages.append(TextSendMessage(text=reply_text))

    # --- 賭局 ---
    elif text == '!搶莊':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(new_deck)
        room['deck'] = new_deck; banker_name = get_user_name(event)
        room['game'] = {'banker_id': user_id, 'banker_name': banker_name, 'game_type': None, 'banker_card_val': None, 'banker_desc': "", 'bets': {}, 'player_results': {}, 'session_log': [], 'played_users': []}
        room['deck'] = [] 
        reply_messages.append(TextSendMessage(text=f"👑 新局開始！莊家：{banker_name}\n❓ 請決定遊戲：輸入「!推」或「!妞妞」\n👉 閒家請「!下注」"))

    elif text == '!下莊':
        game = room['game']; user_name = get_user_name(event)
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 無莊家。"))
        elif user_id != game['banker_id'] and user_id not in ADMINS:
            ts = datetime.now().strftime("%H:%M")
            game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': user_id, 'loser_name': user_name, 'amt': 10000, 'desc': '亂喊下莊罰款', 'time': ts})
            reply_messages.append(TextSendMessage(text=f"😡 {user_name} 亂喊下莊！罰 $10,000"))
        else:
            if not game['session_log']: reply_messages.append(TextSendMessage(text="⚠️ 無輸贏紀錄。"))
            else:
                p_bal = {}; bid = game['banker_id']; bname = game['banker_name']
                for r in game['session_log']:
                    wid, lid, amt = r['winner_id'], r['loser_id'], r['amt']
                    if wid == bid: p_bal[lid] = {'n': r['loser_name'], 'v': p_bal.get(lid, {'v':0})['v'] - amt}
                    elif lid == bid: p_bal[wid] = {'n': r['winner_name'], 'v': p_bal.get(wid, {'v':0})['v'] + amt}
                sum_txt = f"🧾 【總結算 (莊家: @{bname} )】\n----------------\n"; ments = [{"index": sum_txt.find(f"@{bname}"), "length": len(bname)+1, "userId": bid}]
                for pid, d in p_bal.items():
                    net = d['v']; pname = d['n']
                    if net > 0:
                        sp = len(sum_txt) + 8; sum_txt += f"🟥 莊家 給 @{pname} ${net}\n"; ments.append({"index": sp, "length": len(pname)+1, "userId": pid})
                        room['debt'].append({'d': bname, 'c': pname, 'amt': net, 'note': '賭局', 'time': datetime.now().strftime("%H:%M")})
                    elif net < 0:
                        sp = len(sum_txt) + 3; sum_txt += f"🟩 @{pname} 給 莊家 ${abs(net)}\n"; ments.append({"index": sp, "length": len(pname)+1, "userId": pid})
                        room['debt'].append({'d': pname, 'c': bname, 'amt': abs(net), 'note': '賭局', 'time': datetime.now().strftime("%H:%M")})
                sum_txt += "\n✅ 已寫入公帳！\n㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
                msg = TextSendMessage(text=sum_txt, mention={'mentionees': ments})
                game['banker_id'] = None; game['session_log'] = []; game['bets'] = {}
                reply_messages.append(msg)

    elif text.startswith('!下注'):
        game = room['game']
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 無莊家"))
        elif user_id == game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 莊家免下注"))
        elif user_id in game['played_users']: reply_messages.append(TextSendMessage(text="⚠️ 本局已推過"))
        else:
            try:
                parts = text.split(); amount = 100
                if len(parts) > 1 and parts[1].isdigit(): amount = int(parts[1])
                name = get_user_name(event); game['bets'][user_id] = {'amount': amount, 'name': name}
                reply_messages.append(TextSendMessage(text=f"💰 {name} 下注 ${amount}"))
            except: pass

    elif text == '!收牌':
        game = room['game']; deck = room['deck']
        if not game['banker_id']: return
        ts = datetime.now().strftime("%H:%M"); msg = ""
        for pid, info in game['bets'].items():
            if pid not in game['played_users']:
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': pid, 'loser_name': info['name'], 'amt': info['amount'], 'desc': '未開牌', 'time': ts})
                msg += f"💤 {info['name']} 沒開 ❌ 輸 ${info['amount']}\n"
        req = (len(game['bets'])+1) * (2 if game['game_type']=='tui' else 5); shuf = ""
        if len(deck) < req:
            if game['game_type']=='tui': deck = [1,2,3,4,5,6,7,8,9,0.5]*4
            else: deck = [(r,s) for s in ['♠','♥','♦','♣'] for r in range(1,14)]
            random.shuffle(deck); room['deck'] = deck; shuf = "\n🀄 自動洗牌！"
        game['played_users'] = []; game['player_results'] = {}; game['banker_card_val'] = None
        reply_messages.append(TextSendMessage(text=f"🔄 強制結算！{shuf}\n{msg}👉 下一局開始 (剩 {len(deck)} 張)"))

    elif text == '!推' or text == '!妞妞':
        game = room['game']; deck = room['deck']; uid = user_id; name = get_user_name(event)
        cmd = 'tui' if text == '!推' else 'niu'
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 請先 !搶莊"))
        else:
            if not game['game_type']:
                game['game_type'] = cmd
                if cmd == 'tui': room['deck'] = [1,2,3,4,5,6,7,8,9,0.5]*4; msg="🀄 推筒子局！"
                else: room['deck'] = [(r,s) for s in ['♠','♥','♦','♣'] for r in range(1,14)]; msg="🐂 妞妞局！"
                random.shuffle(room['deck']); deck = room['deck']; reply_messages.append(TextSendMessage(text=msg))
            elif game['game_type'] != cmd: return

            if uid in game['played_users']:
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': uid, 'loser_name': name, 'amt': 100, 'desc': '手賤', 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"😡 {name} 重複開牌！罰 $100"))
            elif uid != game['banker_id'] and uid not in game['bets']: reply_messages.append(TextSendMessage(text=f"⚠️ {name} 沒下注"))
            else:
                cn = 2 if game['game_type'] == 'tui' else 5
                if len(deck) < cn:
                    if game['game_type']=='tui': room['deck'] = [1,2,3,4,5,6,7,8,9,0.5]*4
                    else: room['deck'] = [(r,s) for s in ['♠','♥','♦','♣'] for r in range(1,14)]
                    random.shuffle(room['deck']); deck = room['deck']; reply_messages.append(TextSendMessage(text="🔀 自動洗牌！"))
                
                hand = [deck.pop() for _ in range(cn)]; game['played_users'].append(uid)
                if game['game_type'] == 'tui': val=get_tui_value(hand[0],hand[1]); desc=calc_tui_score(hand[0],hand[1]); cstr=f"{get_tile_text(hand[0])} {get_tile_text(hand[1])}"; mult=1
                else: val, desc, mult = calc_niu_score(hand); cstr=" ".join([get_poker_text(c) for c in hand]); desc += f" (x{mult})" if mult>1 else ""

                out = ""; ts = datetime.now().strftime("%H:%M")
                if uid == game['banker_id']:
                    game['banker_card_val']=val; game['banker_desc']=f"{cstr} ({desc})"; out = f"👑 莊家 {name}：\n{game['banker_desc']}\n"
                else:
                    out = f"👤 {name}：\n{cstr} ({desc})\n"; game['player_results'][uid] = {'val': val, 'name': name, 'mult': mult}

                all_b = set(game['bets'].keys()); all_p = set(game['played_users'])
                if game['banker_card_val'] is not None and all_b.issubset(all_p):
                    out += "\n⚔️ 全員到齊！結算：\n"; bv = game['banker_card_val']; bn = game['banker_name']; bm = 1
                    if game['game_type'] == 'niu':
                        if "牛牛" in game['banker_desc']: bm = 3
                        elif "牛8" in game['banker_desc'] or "牛9" in game['banker_desc']: bm = 2
                    
                    for pid in game['bets']:
                        if pid not in game['player_results']: continue
                        pr = game['player_results'][pid]; pv=pr['val']; pn=pr['name']; pm=pr['mult']; amt=game['bets'][pid]['amount']
                        if pv > bv: f=amt*pm; out+=f"✅ {pn} 贏 ${f}\n"; game['session_log'].append({'winner_id':pid, 'winner_name':pn, 'loser_id':game['banker_id'], 'loser_name':bn, 'amt':f, 'desc':'贏', 'time':ts})
                        elif pv < bv: f=amt*bm; out+=f"❌ {pn} 輸 ${f}\n"; game['session_log'].append({'winner_id':game['banker_id'], 'winner_name':bn, 'loser_id':pid, 'loser_name':pn, 'amt':f, 'desc':'輸', 'time':ts})
                        else: out+=f"🤝 {pn} 走水\n"
                    out += f"\n🔄 下一局開始！ (剩 {len(deck)} 張)"; game['played_users']=[]; game['player_results']={}; game['banker_card_val']=None
                elif game['banker_card_val'] is None: out += "(等莊家...)"
                else: out += f"(還有 {len(game['bets'])-len(game['player_results'])} 人...)"
                reply_messages.append(TextSendMessage(text=out))

    # --- 記帳/工具 ---
    elif text.startswith('!記 '):
        try:
            p = text.split(); i = p.index('欠'); d, c, a = p[1], p[i+1], int(p[i+2]); n = " ".join(p[i+3:]) if len(p)>i+3 else "無"
            room['debt'].append({'d':d, 'c':c, 'amt':a, 'note':n, 'time':datetime.now().strftime("%H:%M")})
            reply_messages.append(TextSendMessage(text=f"📝 已記錄：\n{d} 欠 {c} ${a}"))
        except: pass
    elif text.startswith('!還 '):
        try:
            p = text.split(); d, c, a = p[1], p[3], int(p[4])
            room['debt'].append({'d':d, 'c':c, 'amt':-a, 'note':'還款', 'time':datetime.now().strftime("%H:%M")})
            reply_messages.append(TextSendMessage(text=f"💸 已扣除：\n{d} 還 {c} ${a}"))
        except: pass
    elif text == '!查帳':
        if not room['debt']: reply_messages.append(TextSendMessage(text="📭 無欠款紀錄"))
        else:
            s = {}; res = "📊 【欠款總結】\n"
            for r in room['debt']: k=(r['d'],r['c']); s[k]=s.get(k,0)+r['amt']
            for (d,c),t in s.items():
                if t>0: res+=f"🔴 {d} 欠 {c}：${t}\n"
            res += "\n🧾 近期明細：\n"
            for r in room['debt'][-5:]: res += f"[{r['time']}] {r['d']} 欠 {r['c']} ${abs(r['amt'])}\n"
            reply_messages.append(TextSendMessage(text=res))
    elif text == '!一筆勾銷':
        room['debt'].clear(); reply_messages.append(TextSendMessage(text="🧹 帳本已清空！"))
    elif text == '!抓':
        if not room.get('unsent_buffer'): reply_messages.append(TextSendMessage(text="👻 沒人收回"))
        else:
            for item in room['unsent_buffer']:
                if item['type']=='text': reply_messages.append(TextSendMessage(text=f"🕵️ {item['sender']} 收回：\n{item['content']}"))
                elif item['type']=='image': reply_messages.append(ImageSendMessage(original_content_url=item['content'], preview_image_url=item['content']))
            room['unsent_buffer'] = []
    
    # --- 恢復工具箱 ---
    elif text == '!金價':
        try:
            res = requests.get("https://999k.com.tw/", headers=headers, timeout=10); res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, "html.parser"); price_str = None
            for row in soup.find_all('tr'):
                if "黃金賣出" in row.text.strip().replace('\n', '').replace(' ', ''):
                    for td in row.find_all('td'):
                        val = td.text.strip().replace(',', '')
                        if val.isdigit() and len(val) >= 4: price_str = val; break
                if price_str: break
            msg = f"💰 今日金價 (展寬/三井)：\n👉 1錢賣出價：NT$ {price_str}" if price_str else "⚠️ 抓不到價格。"
        except: msg = "⚠️ 抓取金價失敗。"
        reply_messages.append(TextSendMessage(text=msg))
    elif text == '!匯率':
        try:
            res = requests.get("https://rate.bot.com.tw/xrt?Lang=zh-TW", headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser"); found = False
            for row in soup.find('tbody').find_all('tr'):
                if "JPY" in row.text:
                    rate = row.find_all('td')[2].text.strip(); msg = f"🇯🇵 日幣 (JPY) 現金賣出：{rate}"; found=True; break
            if not found: msg = "⚠️ 找不到日幣資料。"
        except: msg = "⚠️ 抓取匯率失敗。"
        reply_messages.append(TextSendMessage(text=msg))
    elif text.startswith('!天氣'):
        q = text.replace('!天氣', '').strip(); lat, lon, loc = 24.9442, 121.2192, "桃園平鎮"
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

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    msg_id = event.message.id; content = line_bot_api.get_message_content(msg_id)
    with open(os.path.join(static_tmp_path, f"{msg_id}.jpg"), 'wb') as fd:
        for chunk in content.iter_content(): fd.write(chunk)

@handler.add(UnsendEvent)
def handle_unsend(event):
    uid = event.unsend.message_id; room = get_room_data(event.source.group_id if event.source.type=='group' else event.source.user_id)
    sender = "有人"
    try: sender = line_bot_api.get_group_member_profile(event.source.group_id, event.source.user_id).display_name if event.source.type=='group' else "有人"
    except: pass
    img = os.path.join(static_tmp_path, f"{uid}.jpg")
    if 'unsent_buffer' not in room: room['unsent_buffer'] = []
    if os.path.exists(img): room['unsent_buffer'].append({'sender':sender, 'type':'image', 'content':f"{FQDN}/static/tmp/{uid}.jpg"})
    elif uid in message_store: room['unsent_buffer'].append({'sender':sender, 'type':'text', 'content':message_store[uid]})

if __name__ == "__main__":
    app.run()
