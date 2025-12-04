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
from googletrans import Translator

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

# 初始化翻譯器
translator = Translator()

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
            'unsent_buffer': [],
            # 賭局狀態
            'game': {
                'banker_id': None,
                'banker_name': None,
                'game_type': None,       # tui 或 niu
                'banker_card_val': None, 
                'banker_desc': "",       
                'bets': {},              
                'player_results': {},    
                'session_log': [],       
                'played_users': []       
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

# --- 遊戲邏輯 ---
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

    # --- 1. 指令：中翻泰 (!泰) ---
    if text.startswith('!泰 '):
        content = text[3:].strip()
        if content:
            try:
                translated = translator.translate(content, dest='th').text
                reply_messages.append(TextSendMessage(text=f"🇹🇭 泰文：\n{translated}"))
            except:
                reply_messages.append(TextSendMessage(text="⚠️ 翻譯失敗。"))

    # --- 2. 被動：泰翻中 (強力版) ---
    elif not text.startswith('!'):
        try:
            # 直接翻譯成中文，不先做 detect (因為 detect 有時候會失敗)
            trans = translator.translate(text, dest='zh-tw')
            
            # 如果 Google 判斷來源是泰文 (th) 且 翻譯結果跟原文不一樣(代表有翻成功)
            if trans.src == 'th' and trans.text != text:
                reply_messages.append(TextSendMessage(text=f"🇹🇭 泰翻中：\n{trans.text}"))
        except Exception as e:
            # 翻譯失敗時安靜跳過，不影響其他功能
            print(f"Translate Debug: {e}")
            pass

    # --- 3. 指令表 ---
    if text == '!指令':
        reply_text = (
            "🤖 機器人指令表：\n"
            "-----------------\n"
            "🇹🇭 翻譯工具\n"
            "👉 !泰 [中文] : 轉成泰文\n"
            "👉 (直接傳泰文) : 自動轉中文\n\n"
            "🎰 流水局 (自動記帳+標記)\n"
            "1. 👉 !搶莊 : 開新大局\n"
            "2. 👉 !下注 200 : 設定下注 (自動延用)\n"
            "3. 👉 !推 : 發牌 (所有人開完秒結算)\n"
            "   ⚠️ 單局重複推 = 罰款$100\n"
            "4. 👉 !收牌 : 強制結算本局\n"
            "5. 👉 !下莊 : 結算大局，寫入公帳\n\n"
            "💰 記帳區\n"
            "👉 !記 / !還 / !查帳 / !一筆勾銷\n"
            "-----------------\n"
            "㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
        )
        reply_messages.append(TextSendMessage(text=reply_text))

    # --- 4. 賭局控制 ---
    elif text == '!搶莊':
        new_deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
        random.shuffle(new_deck)
        room['deck'] = new_deck
        banker_name = get_user_name(event)
        room['game'] = {
            'banker_id': user_id, 'banker_name': banker_name, 'game_type': None,       
            'banker_card_val': None, 'banker_desc': "", 'bets': {}, 'player_results': {},    
            'session_log': [], 'played_users': []       
        }
        room['deck'] = [] 
        reply_messages.append(TextSendMessage(text=f"👑 新局開始！莊家：{banker_name}\n❓ 莊家請決定遊戲：\n🀄 輸入「!推」玩推筒子\n🐂 輸入「!妞妞」玩妞妞\n\n👉 閒家請「!下注」"))

    elif text == '!下莊':
        game = room['game']
        user_name = get_user_name(event)
        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 無莊家。"))
        elif user_id != game['banker_id']:
            timestamp = datetime.now().strftime("%H:%M")
            game['session_log'].append({
                'winner_id': game['banker_id'], 'winner_name': game['banker_name'],
                'loser_id': user_id, 'loser_name': user_name,
                'amt': 10000, 'desc': '亂喊下莊罰款', 'time': timestamp
            })
            reply_messages.append(TextSendMessage(text=f"😡 {user_name} 亂喊下莊！罰 $10,000"))
        else:
            if not game['session_log']:
                reply_messages.append(TextSendMessage(text="⚠️ 本次大局沒有輸贏紀錄。"))
            else:
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
                if not player_balances: summary_text += "🤝 大家打平！\n"
                else:
                    for pid, info in player_balances.items():
                        net = info['net']; pname = info['name']
                        if net > 0: 
                            start = len(summary_text); summary_text += "🟥 莊家 給 "; start_p = len(summary_text)
                            summary_text += f"@{pname}"; summary_mentions.append({"index": start_p, "length": len(pname)+1, "userId": pid})
                            summary_text += f" ${net}\n"; count += 1
                            room['debt'].append({'d': banker_name, 'c': pname, 'amt': net, 'note': '賭局結算', 'time': datetime.now().strftime("%H:%M")})
                        elif net < 0:
                            start = len(summary_text); summary_text += "🟩 "; start_p = len(summary_text)
                            summary_text += f"@{pname}"; summary_mentions.append({"index": start_p, "length": len(pname)+1, "userId": pid})
                            summary_text += f" 給 莊家 ${abs(net)}\n"; count += 1
                            room['debt'].append({'d': pname, 'c': banker_name, 'amt': abs(net), 'note': '賭局結算', 'time': datetime.now().strftime("%H:%M")})

                summary_text += "\n✅ 已寫入公帳！\n㊗️黃燜雞楊梅店,黃金當鋪,JC Beauty生意興榮㊗️"
                msg = TextSendMessage(text=summary_text, mention={'mentionees': summary_mentions})
                game['banker_id'] = None; game['session_log'] = []; game['bets'] = {}
                reply_messages.append(msg)

    elif text.startswith('!下注'):
        game = room['game']
        if not game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 沒人做莊！"))
        elif user_id == game['banker_id']: reply_messages.append(TextSendMessage(text="⚠️ 莊家不能下注"))
        elif user_id in game['played_users']: reply_messages.append(TextSendMessage(text="⚠️ 本局已推牌，下局生效"))
        else:
            try:
                parts = text.split(); amount = 100
                if len(parts) > 1 and parts[1].isdigit(): amount = int(parts[1])
                player_name = get_user_name(event)
                game['bets'][user_id] = {'amount': amount, 'name': player_name}
                reply_messages.append(TextSendMessage(text=f"💰 {player_name} 下注 ${amount}"))
            except: pass

    elif text == '!收牌':
        game = room['game']; deck = room['deck']
        if not game['banker_id']: return
        
        missing_text = ""; timestamp = datetime.now().strftime("%H:%M")
        for pid, info in game['bets'].items():
            if pid not in game['played_users']:
                amt = info['amount']; p_name = info['name']
                missing_text += f"💤 {p_name} 沒開 ❌ 輸 ${amt}\n"
                game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': pid, 'loser_name': p_name, 'amt': amt, 'desc': '未開牌', 'time': timestamp})

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

    elif text == '!推' or text == '!妞妞':
        game = room['game']; user_name = get_user_name(event); deck = room['deck']
        current_command = 'tui' if text == '!推' else 'niu'

        if not game['banker_id']:
            reply_messages.append(TextSendMessage(text="⚠️ 請先「!搶莊」"))
        else:
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

            if user_id in game['played_users']:
                log = {'winner_id': game['banker_id'], 'winner_name': game['banker_name'], 'loser_id': user_id, 'loser_name': user_name, 'amt': 100, 'desc': '手賤罰款', 'time': datetime.now().strftime("%H:%M")}
                game['session_log'].append(log)
                reply_messages.append(TextSendMessage(text=f"😡 {user_name} 重複開牌！罰 $100"))
            elif user_id != game['banker_id'] and user_id not in game['bets']:
                reply_messages.append(TextSendMessage(text=f"⚠️ {user_name} 沒下注不能玩！"))
            else:
                cards_needed = 2 if game['game_type'] == 'tui' else 5
                if len(deck) < cards_needed:
                    if game['game_type'] == 'tui': room['deck'] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0.5] * 4
                    else: room['deck'] = [(r, s) for s in ['♠','♥','♦','♣'] for r in range(1, 14)]
                    random.shuffle(room['deck']); deck = room['deck']
                    reply_messages.append(TextSendMessage(text="🔀 牌不夠了，自動洗牌！"))

                hand = [deck.pop() for _ in range(cards_needed)]
                game['played_users'].append(user_id)
                val, desc, mult = 0, "", 1
                if game['game_type'] == 'tui':
                    val = get_tui_value(hand[0], hand[1]); desc = calc_tui_score(hand[0], hand[1])
                    card_str = f"{get_tile_text(hand[0])} {get_tile_text(hand[1])}"
                else:
                    val, desc, mult = calc_niu_score(hand)
                    card_str = " ".join([get_poker_text(c) for c in hand])
                    if mult > 1: desc += f" (x{mult})"

                if user_id == game['banker_id']:
                    game['banker_card_val'] = val; game['banker_desc'] = f"{card_str} ({desc})"
                    output_msg = f"👑 莊家 {user_name}：\n{game['banker_desc']}\n"
                else:
                    output_msg = f"👤 {user_name}：\n{card_str} ({desc})\n"
                    game['player_results'][user_id] = {'val': val, 'name': user_name, 'mult': mult}

                all_bets = set(game['bets'].keys()); all_played = set(game['played_users'])
                if game['banker_card_val'] is not None and all_bets.issubset(all_played):
                    output_msg += "\n⚔️ 全員到齊！結算：\n"
                    b_val = game['banker_card_val']; b_name = game['banker_name']; b_mult = 1
                    if game['game_type'] == 'niu':
                        if "牛牛" in game['banker_desc']: b_mult = 3
                        elif "牛8" in game['banker_desc'] or "牛9" in game['banker_desc']: b_mult = 2
                    timestamp = datetime.now().strftime("%H:%M")

                    for pid in game['bets']:
                        if pid not in game['player_results']: continue
                        p_res = game['player_results'][pid]
                        p_val, p_name, p_mult = p_res['val'], p_res['name'], p_res['mult']
                        base_amt = game['bets'][pid]['amount']
                        
                        if p_val > b_val:
                            final_amt = base_amt * p_mult
                            output_msg += f"✅ {p_name} 贏 ${final_amt}\n"
                            game['session_log'].append({'winner_id': pid, 'winner_name': p_name, 'loser_id': game['banker_id'], 'loser_name': b_name, 'amt': final_amt, 'desc': '閒贏', 'time': timestamp})
                        elif p_val < b_val:
                            final_amt = base_amt * b_mult
                            output_msg += f"❌ {p_name} 輸 ${final_amt}\n"
                            game['session_log'].append({'winner_id': game['banker_id'], 'winner_name': b_name, 'loser_id': pid, 'loser_name': p_name, 'amt': final_amt, 'desc': '莊贏', 'time': timestamp})
                        else: output_msg += f"🤝 {p_name} 走水\n"

                    output_msg += f"\n🔄 自動開始下一局！ (剩 {len(deck)} 張)"
                    game['played_users'] = []; game['player_results'] = {}; game['banker_card_val'] = None; game['banker_desc'] = ""
                elif game['banker_card_val'] is None: output_msg += "(等莊家...)"
                else: output_msg += f"(還有 {len(game['bets']) - len(game['player_results'])} 人...)"
                reply_messages.append(TextSendMessage(text=output_msg))

    # --- 記帳/工具 ---
    elif text.startswith('!記 '):
        try:
            parts = text.split(); idx = parts.index('欠')
            d, c, amt = parts[1], parts[idx+1], int(parts[idx+2])
            note = " ".join(parts[idx+3:]) if len(parts) > idx+3 else "無備註"
            room['debt'].append({'d': d, 'c': c, 'amt': amt, 'note': note, 'time': datetime.now().strftime("%H:%M")})
            reply_messages.append(TextSendMessage(text=f"📝 [本群] 已記錄：\n{d} 欠 {c} ${amt}\n({note})"))
        except: pass
    elif text == '!查帳':
        if not room['debt']:
            reply_messages.append(TextSendMessage(text="📭 [本群] 目前沒有欠款紀錄！"))
        else:
            summary = {}; res = "📊 【本群欠款總結】\n"
            for r in room['debt']:
                k = (r['d'], r['c']); summary[k] = summary.get(k, 0) + r['amt']
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
        room['debt'].clear(); reply_messages.append(TextSendMessage(text="🧹 [本群] 帳本已清空！"))
    elif text == '!抓': # 抓收回
        if not room.get('unsent_buffer'): reply_messages.append(TextSendMessage(text="👻 目前沒有人收回訊息喔！"))
        else:
            for item in room['unsent_buffer']:
                sender = item['sender']; msg_type = item['type']; content = item['content']
                if msg_type == 'text': reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！「{sender}」收回：\n{content}"))
                elif msg_type == 'image':
                    img_url = content
                    reply_messages.append(TextSendMessage(text=f"🕵️ 抓到了！「{sender}」收回圖片 👇"))
                    reply_messages.append(ImageSendMessage(original_content_url=img_url, preview_image_url=img_url))
            room['unsent_buffer'] = []
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

# --- 處理圖片/收回 ---
@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    msg_id = event.message.id
    content = line_bot_api.get_message_content(msg_id)
    with open(os.path.join(static_tmp_path, f"{msg_id}.jpg"), 'wb') as fd:
        for chunk in content.iter_content(): fd.write(chunk)

@handler.add(UnsendEvent)
def handle_unsend(event):
    uid = event.unsend.message_id
    source_id = event.source.group_id if event.source.type == 'group' else event.source.user_id
    room = get_room_data(source_id)
    sender_name = "有人"
    try:
        user_id = event.source.user_id
        if event.source.type == 'group': sender_name = line_bot_api.get_group_member_profile(event.source.group_id, user_id).display_name
        else: sender_name = line_bot_api.get_profile(user_id).display_name
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
