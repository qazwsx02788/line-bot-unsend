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
        # 預設先給推筒子牌堆，之後搶莊會重洗
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
                'game_type': None,       # tui 或 niu
                'banker_card_val': None, # 莊家點數/權重
                'banker_desc': "",       # 莊家牌面文字
                'bets': {},              # 下注池
                'player_results': {},    # 本局閒家暫存
                'session_log': [],       # 大局流水帳
                'played_users': []       # 本小局已開牌名單
            }
        }
    return rooms_data[source_id]

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

# ----------------------------------------------------
# 🀄 推筒子邏輯
# ----------------------------------------------------
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

# ----------------------------------------------------
# 🐂 妞妞邏輯
# ----------------------------------------------------
def get_poker_text(card):
    rank, suit = card
    r_text = {1:'A', 11:'J', 12:'Q', 13:'K'}.get(rank, str(rank))
    return f"{suit}{r_text}"

def calc_niu_score(hand):
    values = []
    for r, s in hand:
        v = 10 if r >= 10 else r
        values.append(v)
    
    total = sum(values)
    niu_point = -1 
    for i in range(5):
        for j in range(i+1, 5):
            rem = values[i] + values[j]
            if (total - rem) % 10 == 0:
                np = rem % 10
                if np == 0: np = 10 
                if np > niu_point: niu_point = np
    
    # 倍率設定
    if niu_point == -1: return 0, "💩 無牛", 1
    elif niu_point == 10: return 100, "🎉 牛牛", 3
    else:
        multiplier = 2 if niu_point >= 8 else 1
        return niu_point * 10, f"🐂 牛{niu_point}", multiplier

def get_user_name(event, user_id=None):
    if not user_id: user_id = event.source.user_id
    try:
        if event.source.type == 'group':
            return line_bot_api.get_group_member_profile(event.source.group_id, user_id).display_name
        else:
            return line_bot_api.get_profile(user_id).display_name
    except: return "玩家"

# --- 處理訊息 ---
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
            "🤖 機器人指令表：\n"
            "-----------------\n"
            "🎰 雙模式賭場 (自動記帳)\n"
            "1. 👉 !搶莊 : 開新大局\n"
            "2. 👉 !下注 200 : 閒家下注\n"
            "3. 決定遊戲 (莊家喊，鎖定至下莊):\n"
            "   🀄 👉 !推 (推筒子)\n"
            "   🐂 👉 !妞妞 (撲克牌)\n"
            "   (妞妞倍率: 牛牛x3, 牛8/9x2, 其他x1)\n"
            "4. 👉 !收牌 : 強制結算本局\n"
            "5. 👉 !下莊 : 結算大局，寫入公帳\n"
            "   (⚠️ 亂喊下莊罰 $10000)\n\n"
            "💰 記帳區\n"
            "👉 !記 / !還 / !查帳 / !一筆勾銷\n"
            "-----------------\n"
            "㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
        )
        reply_messages.append(TextSendMessage(text=reply_text))

    # --- 🎰 大局控制 ---
    elif text == '!搶莊':
        banker_name = get_user_name(event)
        room['game'] = {
            'banker_id': user_id,
            'banker_name': banker_name,
            'game_type': None,       
            'banker_card_val': None, 
            'banker_desc': "",       
            'bets': {},              
            'player_results': {},    
            'session_log': [],       
            'played_users': []       
        }
        room['deck'] = [] # 清空牌堆，等決定遊戲再洗
        reply_messages.append(TextSendMessage(text=f"👑 新局開始！莊家：{banker_name}\n❓ 莊家請決定遊戲：輸入「!推」或「!妞妞」\n👉 閒家請「!下注」"))

    elif text == '!下莊':
        game = room['game']
        user_name = get_user_name(event)

        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 目前無莊家。"))
        
        # 🚨 權限檢查：只有莊家能下莊
        elif user_id != game['banker_id']:
            timestamp = datetime.now().strftime("%H:%M")
            # 罰款記入大局流水帳
            game['session_log'].append({
                'winner_id': game['banker_id'], 'winner_name': game['banker_name'],
                'loser_id': user_id, 'loser_name': user_name,
                'amt': 10000, 
                'desc': '亂喊下莊罰款', 
                'time': timestamp
            })
            reply_messages.append(TextSendMessage(text=f"😡 {user_name} 你不是莊家喊什麼下莊！\n💸 罰款 $10,000 (已記入莊家帳上)"))

        # ✅ 合法下莊
        else:
            if not game['session_log']:
                reply_messages.append(TextSendMessage(text="⚠️ 本次大局沒有輸贏紀錄。"))
            else:
                # 淨額結算
                player_balances = {} 
                banker_name = game['banker_name']; banker_id = game['banker_id']
                for r in game['session_log']:
                    wid, wname, lid, lname, amt = r['winner_id'], r['winner_name'], r['loser_id'], r['loser_name'], r['amt']
                    if wid == banker_id:
                        if lid not in player_balances: player_balances[lid] = {'name': lname, 'net': 0}
                        player_balances[lid]['net'] -= amt
                    elif lid == banker_id:
                        if wid not in player_balances: player_balances[wid] = {'name': wname, 'net': 0}
                        player_balances[wid]['net'] += amt

                summary_text = f"🧾 【總結算 (莊家: @{banker_name} )】\n----------------\n"
                summary_mentions = []
                summary_mentions.append({"index": summary_text.find(f"@{banker_name}"), "length": len(banker_name)+1, "userId": banker_id})
                
                count = 0
                if not player_balances:
                    summary_text += "🤝 大家打平！\n"
                else:
                    for pid, info in player_balances.items():
                        net = info['net']
                        pname = info['name']
                        if net > 0: # 閒贏
                            start = len(summary_text); summary_text += "🟥 莊家 給 "; start_p = len(summary_text)
                            summary_text += f"@{pname}"; summary_mentions.append({"index": start_p, "length": len(pname)+1, "userId": pid})
                            summary_text += f" ${net}\n"
                            room['debt'].append({'d': banker_name, 'c': pname, 'amt': net, 'note': '賭局結算', 'time': datetime.now().strftime("%H:%M")})
                            count += 1
                        elif net < 0: # 閒輸
                            start = len(summary_text); summary_text += "🟩 "; start_p = len(summary_text)
                            summary_text += f"@{pname}"; summary_mentions.append({"index": start_p, "length": len(pname)+1, "userId": pid})
                            summary_text += f" 給 莊家 ${abs(net)}\n"
                            room['debt'].append({'d': pname, 'c': banker_name, 'amt': abs(net), 'note': '賭局結算', 'time': datetime.now().strftime("%H:%M")})
                            count += 1

                summary_text += "\n✅ 已寫入公帳！\n㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
                msg = TextSendMessage(text=summary_text, mention={'mentionees': summary_mentions})
                
                game['banker_id'] = None
                game['session_log'] = []
                game['bets'] = {}
                reply_messages.append(msg)

    # --- 🃏 下注 ---
    elif text.startswith('!下注'):
        game = room['game']
        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 沒人做莊！"))
        elif user_id == game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 莊家不能下注"))
        elif user_id in game['played_users']:
            reply_messages.append(TextSendMessage(text="⚠️ 本局已推牌，下局生效"))
        else:
            try:
                parts = text.split(); amount = 100
                if len(parts) > 1 and parts[1].isdigit(): amount = int(parts[1])
                player_name = get_user_name(event)
                game['bets'][user_id] = {'amount': amount, 'name': player_name}
                reply_messages.append(TextSendMessage(text=f"💰 {player_name} 下注 ${amount}"))
            except: pass

    # --- 🃏 強制收牌 ---
    elif text == '!收牌':
        game = room['game']
        deck = room['deck']
        if not game['banker_id']: return
        
        # 沒開牌判輸
        missing_text = ""; timestamp = datetime.now().strftime("%H:%M")
        for pid, info in game['bets'].items():
            if pid not in game['played_users']:
                amt = info['amount']; p_name = info['name']
                missing_text += f"💤 {p_name} 沒開 ❌ 輸 ${amt}\n"
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': pid, 'loser_name': p_name, 'amt': amt, 'desc': '未開牌', 'time': timestamp})

        # 洗牌檢查
        shuffle_msg = ""
        cards_needed = 2 if game['game_type'] == 'tui' else 5
        needed = (len(game['bets']) + 1) * cards_needed
        if len(room['deck']) < needed:
            if game['game_type'] == 'tui': room['deck'] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
            else: room['deck'] = [(r, s) for s in ['♠','♥','♦','♣'] for r in range(1, 14)]
            random.shuffle(room['deck'])
            shuffle_msg = "\n🀄 牌底不足，已自動洗牌！"

        game['played_users'] = []; game['player_results'] = {}; game['banker_card_val'] = None; game['banker_desc'] = ""
        reply_messages.append(TextSendMessage(text=f"🔄 強制結算！{shuffle_msg}\n{missing_text}👉 下一局開始！(剩 {len(room['deck'])} 張)"))

    # --- 🀄 遊戲核心 ---
    elif text == '!推' or text == '!妞妞':
        game = room['game']
        user_name = get_user_name(event)
        deck = room['deck']
        current_command = 'tui' if text == '!推' else 'niu'

        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 請先「!搶莊」"))
        else:
            # 1. 決定遊戲類型
            if game['game_type'] is None:
                game['game_type'] = current_command
                if current_command == 'tui':
                    room['deck'] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4; random.shuffle(room['deck'])
                    reply_messages.append(TextSendMessage(text="🀄 決定玩「推筒子」！牌已洗好。"))
                else:
                    room['deck'] = [(r, s) for s in ['♠','♥','♦','♣'] for r in range(1, 14)]; random.shuffle(room['deck'])
                    reply_messages.append(TextSendMessage(text="🐂 決定玩「妞妞」！牌已洗好。"))
                deck = room['deck']

            elif game['game_type'] != current_command:
                game_name = "推筒子" if game['game_type'] == 'tui' else "妞妞"
                reply_messages.append(TextSendMessage(text=f"🚫 本局鎖定為「{game_name}」！直到下莊才能換。"))
                line_bot_api.reply_message(event.reply_token, reply_messages); return 

            # 2. 罰款檢查
            if user_id in game['played_users']:
                log = {'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': user_id, 'loser_name': user_name, 'amt': 100, 'desc':
