import os
import random
import requests
import threading
import time
import traceback
from datetime import datetime
from bs4 import BeautifulSoup
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    ImageMessage, ImageSendMessage, UnsendEvent, JoinEvent
)
from googletrans import Translator

app = Flask(__name__)

# ==========================================
# 👇 1. 請改成你的 Render 網址
FQDN = "https://line-bot-unsend.onrender.com"

# 👇 2. 請填入「你的」User ID (最高權限老闆)
OWNER_ID = "U6d111042c6ecb593b8c6bb781417c45f" 

# 👇 3. 電腦連線密碼
API_PASSWORD = "0208"
# ==========================================

token = os.environ.get('CHANNEL_ACCESS_TOKEN')
secret = os.environ.get('CHANNEL_SECRET')
line_bot_api = LineBotApi(token)
handler = WebhookHandler(secret)

translator = Translator()
message_store = {}
static_tmp_path = os.path.join(os.path.dirname(__file__), 'static', 'tmp')
os.makedirs(static_tmp_path, exist_ok=True)
rooms_data = {}

# --- 全域變數管理 ---
ADMINS = {OWNER_ID} 
BLACKLIST = set()
# 預設祝賀詞
CUSTOM_FOOTER = "㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"

def get_room_data(source_id):
    if source_id not in rooms_data:
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        rooms_data[source_id] = {
            'debt': [], 'deck': new_deck, 'unsent_buffer': [],
            'outsider_warn': {}, 
            'game': {
                'banker_id': None, 'banker_name': None, 'game_type': None,
                'banker_card_val': None, 'banker_desc': "", 'bets': {},
                'player_results': {}, 'session_log': [], 'played_users': [],
                'betting_locked': False, 'session_locked': False, 'allowed_players': set(),
                'round_id': 0
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

# --- 🔌 超級控制台 API ---
@app.route("/api/control", methods=['POST'])
def api_control():
    global CUSTOM_FOOTER
    data = request.json
    pwd = data.get('password')
    cmd = data.get('command')
    payload = data.get('payload', {})

    if pwd != API_PASSWORD:
        return jsonify({"status": "error", "message": "密碼錯誤"}), 403

    # 1. 獲取所有狀態
    if cmd == "get_status":
        return jsonify({
            "status": "ok",
            "footer": CUSTOM_FOOTER,
            "blacklist": list(BLACKLIST),
            "active_groups": list(rooms_data.keys())
        })

    # 2. 修改祝賀詞
    elif cmd == "set_footer":
        new_footer = payload.get('footer')
        if new_footer:
            CUSTOM_FOOTER = new_footer
            return jsonify({"status": "ok", "message": "祝賀詞已更新"})

    # 3. 黑名單管理
    elif cmd == "blacklist_add":
        uid = payload.get('user_id')
        if uid: BLACKLIST.add(uid)
        return jsonify({"status": "ok", "message": f"已封鎖 {uid}"})
    
    elif cmd == "blacklist_remove":
        uid = payload.get('user_id')
        if uid and uid in BLACKLIST: BLACKLIST.remove(uid)
        return jsonify({"status": "ok", "message": f"已解鎖 {uid}"})

    # 4. 廣播
    elif cmd == "broadcast":
        msg = payload.get('message')
        count = 0
        if msg:
            for gid in rooms_data:
                try:
                    line_bot_api.push_message(gid, TextSendMessage(text=f"📢 [公告] {msg}"))
                    count += 1
                except: pass
        return jsonify({"status": "ok", "message": f"已發送給 {count} 個群組"})

    # 5. 強制重置
    elif cmd == "reset_game":
        gid = payload.get('group_id')
        if gid and gid in rooms_data:
            # 重置該群組
            new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(new_deck)
            rooms_data[gid]['deck'] = new_deck
            rooms_data[gid]['game'] = {
                'banker_id': None, 'banker_name': None, 'game_type': None,
                'banker_card_val': None, 'banker_desc': "", 'bets': {},
                'player_results': {}, 'session_log': [], 'played_users': [],
                'betting_locked': False, 'session_locked': False, 'allowed_players': set(),
                'round_id': rooms_data[gid]['game'].get('round_id', 0) + 1
            }
            return jsonify({"status": "ok", "message": "該群組賭局已重置"})

    return jsonify({"status": "error", "message": "未知指令"})


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    except Exception as e: print(f"Error: {e}"); return 'OK'
    return 'OK'

# --- 遊戲與工具邏輯 (保持不變) ---
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

def round_timer_thread(group_id, check_round_id):
    time.sleep(15)
    room = get_room_data(group_id); game = room['game']
    if game['round_id'] != check_round_id or not game['banker_id'] or game['banker_card_val'] is None: return
    unplayed = [pid for pid in game['bets'] if pid not in game['played_users']]
    if unplayed:
        try: line_bot_api.push_message(group_id, TextSendMessage(text=f"⏰ 還有 {len(unplayed)} 人未開牌！剩 5 秒判輸！"))
        except: pass
    else: return
    time.sleep(5)
    if game['round_id'] != check_round_id or not game['banker_id']: return
    missing_text = ""; ts = datetime.now().strftime("%H:%M"); has_penalty = False
    for pid, info in game['bets'].items():
        if pid not in game['played_users']:
            amt = info['amount']; p_name = info['name']
            missing_text += f"💤 {p_name} 超時未開 ❌ 輸 ${amt}\n"
            game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': pid, 'loser_name': p_name, 'amt': amt, 'desc': '超時判輸', 'time': ts})
            game['played_users'].append(pid); has_penalty = True
    if has_penalty:
        check_and_settle(group_id, room)
        if missing_text:
            try: line_bot_api.push_message(group_id, TextSendMessage(text=f"⌛ 時間到！\n{missing_text}"))
            except: pass

def check_and_settle(group_id, room):
    game = room['game']
    all_bets = set(game['bets'].keys())
    if game['banker_card_val'] is not None and all_bets.issubset(set(game['played_users'])):
        output_msg = "\n⚔️ 本局結算：\n"; b_val = game['banker_card_val']; b_name = game['banker_name']; b_mult = 1
        if game['game_type'] == 'niu':
            if "牛牛" in game['banker_desc']: b_mult = 3
            elif "牛8" in game['banker_desc'] or "牛9" in game['banker_desc']: b_mult = 2
        ts = datetime.now().strftime("%H:%M")
        for pid in game['bets']:
            if pid not in game['player_results']: continue 
            p_res = game['player_results'][pid]; p_val = p_res['val']; p_name = p_res['name']; p_mult = p_res['mult']; base_amt = game['bets'][pid]['amount']
            if p_val > b_val:
                final_amt = base_amt * p_mult; output_msg += f"✅ {p_name} 贏 ${final_amt}\n"
                game['session_log'].append({'winner_id': pid, 'winner_name': p_name, 'loser_id': game['banker_id'], 'loser_name': b_name, 'amt': final_amt, 'desc': '閒贏', 'time': ts})
            elif p_val < b_val:
                final_amt = base_amt * b_mult; output_msg += f"❌ {p_name} 輸 ${final_amt}\n"
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': b_name, 'loser_id': pid, 'loser_name': p_name, 'amt': final_amt, 'desc': '莊贏', 'time': ts})
            else: output_msg += f"🤝 {p_name} 走水\n"
        output_msg += f"\n🔄 自動開始下一局！ (剩 {len(room['deck'])} 張)"
        if not game['session_locked']:
            game['session_locked'] = True; game['allowed_players'] = set(game['bets'].keys())
            output_msg += "\n🔒 玩家名單已鎖定！"
        game['played_users'] = []; game['player_results'] = {}; game['banker_card_val'] = None; game['banker_desc'] = ""; game['round_id'] += 1
        try: line_bot_api.push_message(group_id, TextSendMessage(text=output_msg))
        except: pass

@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    msg_id = event.message.id; text = event.message.text.strip()
    user_id = event.source.user_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    
    if user_id in BLACKLIST: return 
    room = get_room_data(source_id); message_store[msg_id] = text
    reply_messages = []

    # 指令表
    if text == '!指令':
        reply_text = (
            "🤖 機器人指令表：\n-----------------\n"
            "🎰 流水局\n1. !搶莊\n2. !下注 200\n3. !推 (推筒/妞妞)\n4. !停 / !收牌\n5. !下莊 (亂喊罰一萬)\n\n"
            "🇹🇭 翻譯\n👉 !泰 [文] / 傳泰文自動翻\n\n"
            "💰 記帳\n👉 !記 / !還 / !查帳 / !一筆勾銷\n👉 !抓 (防收回)\n👉 !金價 / !匯率 / !天氣\n-----------------\n"
            f"{CUSTOM_FOOTER}"
        )
        reply_messages.append(TextSendMessage(text=reply_text))
    
    # 賭局指令
    elif text == '!搶莊':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(new_deck)
        room['deck'] = new_deck; banker_name = get_user_name(event)
        room['game'] = {'banker_id': user_id, 'banker_name': banker_name, 'game_type': None, 'banker_card_val': None, 'banker_desc': "", 'bets': {}, 'player_results': {}, 'session_log': [], 'played_users': [], 'betting_locked': False, 'session_locked': False, 'allowed_players': set(), 'round_id': 0}
        room['deck'] = [] 
        reply_messages.append(TextSendMessage(text=f"👑 新局開始！莊家：{banker_name}\n❓ 請決定遊戲：輸入「!推」或「!妞妞」"))
    
    elif text == '!下莊':
        game = room['game']; user_name = get_user_name(event)
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 無莊家"))
        elif user_id != game['banker_id'] and user_id not in ADMINS:
            ts = datetime.now().strftime("%H:%M")
            game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': user_id, 'loser_name': user_name, 'amt': 10000, 'desc': '亂喊下莊罰款', 'time': ts})
            reply_messages.append(TextSendMessage(text=f"😡 {user_name} 亂喊下莊！罰 $10,000"))
        else:
            if not game['session_log']: reply_messages.append(TextSendMessage(text="⚠️ 無輸贏紀錄"))
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
                        s = len(sum_txt) + 8; sum_txt += f"🟥 莊家 給 @{pname} ${net}\n"; ments.append({"index": s, "length": len(pname)+1, "userId": pid})
                        room['debt'].append({'d': bname, 'c': pname, 'amt': net, 'note': '賭局', 'time': datetime.now().strftime("%H:%M")})
                    elif net < 0:
                        s = len(sum_txt) + 3; sum_txt += f"🟩 @{pname} 給 莊家 ${abs(net)}\n"; ments.append({"index": s, "length": len(pname)+1, "userId": pid})
                        room['debt'].append({'d': pname, 'c': bname, 'amt': abs(net), 'note': '賭局', 'time': datetime.now().strftime("%H:%M")})
                sum_txt += f"\n✅ 已寫入公帳！\n{CUSTOM_FOOTER}"
                msg = TextSendMessage(text=sum_txt, mention={'mentionees': ments})
                game['banker_id'] = None; game['session_log'] = []; game['bets'] = {}
                reply_messages.append(msg)

    elif text == '!停':
        game = room['game']
        if user_id == game['banker_id'] or user_id in ADMINS: game['betting_locked'] = True; reply_messages.append(TextSendMessage(text="🛑 停止下注！"))
        else: reply_messages.append(TextSendMessage(text="🚫 你不是莊家"))
    
    elif text.startswith('!下注'):
        game = room['game']
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 無莊家"))
        elif game['betting_locked']: reply_messages.append(TextSendMessage(text="🛑 下注已鎖定"))
        elif user_id == game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 莊家免下注"))
        elif user_id in game['played_users']: reply_messages.append(TextSendMessage(text="⚠️ 本局已推過"))
        elif game['session_locked'] and user_id not in game['allowed_players']:
            wc = room['outsider_warn'].get(user_id, 0) + 1; room['outsider_warn'][user_id] = wc; name = get_user_name(event)
            if wc == 1: reply_messages.append(TextSendMessage(text=f"⚠️ {name} 遊戲鎖定，路人勿擾(1次)"))
            elif wc == 2:
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': user_id, 'loser_name': name, 'amt': 200, 'desc': '路人罰款', 'time': datetime.now().strftime("%H:%M")})
                reply_messages.append(TextSendMessage(text=f"😡 {name} 講不聽！罰款 $200"))
            else: reply_messages.append(TextSendMessage(text=f"🤬 死小孩講不聽是不是！"))
        else:
            try:
                parts = text.split(); amount = 100
                if len(parts) > 1 and parts[1].isdigit(): amount = int(parts[1])
                name = get_user_name(event); game['bets'][user_id] = {'amount': amount, 'name': name}
                reply_messages.append(TextSendMessage(text=f"💰 {name} 下注 ${amount}"))
            except: pass

    elif text == '!推' or text == '!妞妞':
        game = room['game']; deck = room['deck']; uid = user_id; name = get_user_name(event); cmd = 'tui' if text == '!推' else 'niu'
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 請先 !搶莊"))
        else:
            if not game['game_type']:
                game['game_type'] = cmd
                if cmd == 'tui': room['deck'] = [1,2,3,4,5,6,7,8,9,0.5]*4; msg="🀄 推筒子局！"
                else: room['deck'] = [(r,s) for s in ['♠','♥','♦','♣'] for r in range(1,14)]; msg="🐂 妞妞局！"
                random.shuffle(room['deck']); deck = room['deck']; reply_messages.append(TextSendMessage(text=msg))
            elif game['game_type'] != cmd: return
            if uid in game['played_users']:
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': uid, 'loser_name': name, 'amt': 100, 'desc': '手賤罰款', 'time': datetime.now().strftime("%H:%M")})
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
                if uid == game['banker_id']:
                    game['banker_card_val']=val; game['banker_desc']=f"{cstr} ({desc})"
                    reply_messages.append(TextSendMessage(text=f"👑 莊家 {name}：\n{game['banker_desc']}\n"))
                    threading.Thread(target=round_timer_thread, args=(source_id, game['round_id']), daemon=True).start()
                else:
                    reply_messages.append(TextSendMessage(text=f"👤 {name}：\n{cstr} ({desc})\n")); game['player_results'][uid] = {'val': val, 'name': name, 'mult': mult}
                check_and_settle(source_id, room)
    
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

    # --- 翻譯/工具 ---
    elif text.startswith('!泰 '):
        try: reply_messages.append(TextSendMessage(text=f"🇹🇭 泰文：\n{translator.translate(text[3:].strip(), dest='th').text}"))
        except: pass
    elif not text.startswith('!'):
        try:
            if translator.detect(text).lang == 'th':
                res = translator.translate(text, src='th', dest='zh-tw')
                if res.text != text: reply_messages.append(TextSendMessage(text=f"🇹🇭 泰翻中：\n{res.text}"))
        except: pass

    # --- 記帳 ---
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
        if not room['debt']: reply_messages.append(TextSendMessage(text="📭 無欠款"))
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
    
    # --- 金價匯率天氣 ---
    elif text == '!金價':
        try:
            res = requests.get("https://999k.com.tw/", headers=headers, timeout=10); res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, "html.parser"); price = None
            for row in soup.find_all('tr'):
                if "黃金賣出" in row.text.strip().replace('\n','').replace(' ',''):
                    for td in row.find_all('td'):
                        val = td.text.strip().replace(',','')
                        if val.isdigit() and len(val)>=4: price = val; break
                if price: break
            msg = f"💰 今日金價 (展寬/三井)：\n👉 1錢賣出價：NT$ {price}" if price else "⚠️ 抓不到價格"
            reply_messages.append(TextSendMessage(text=msg))
        except: pass
    elif text == '!匯率':
        try:
            res = requests.get("https://rate.bot.com.tw/xrt?Lang=zh-TW", headers=headers, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser"); found = False
            for row in soup.find('tbody').find_all('tr'):
                if "JPY" in row.text:
                    rate = row.find_all('td')[2].text.strip(); reply_messages.append(TextSendMessage(text=f"🇯🇵 日幣現金賣出：{rate}")); found=True; break
            if not found: reply_messages.append(TextSendMessage(text="⚠️ 抓不到匯率"))
        except: pass
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
