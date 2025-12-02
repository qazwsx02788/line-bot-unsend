import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, UnsendEvent

app = Flask(__name__)

# 讀取金鑰
line_bot_api = LineBotApi(os.environ.get('CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.environ.get('CHANNEL_SECRET'))

# 暫存訊息的地方 (重啟會消失)
message_store = {}

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 存訊息：ID 當鑰匙，內容當值
    message_store[event.message.id] = event.message.text

@handler.add(UnsendEvent)
def handle_unsend(event):
    # 抓收回
    unsent_id = event.unsend.message_id
    if unsent_id in message_store:
        msg = message_store[unsent_id]
        # 回傳到群組
        if event.source.type == 'group':
            line_bot_api.push_message(event.source.group_id, TextSendMessage(text=f"有人收回訊息：\n{msg}"))
        elif event.source.type == 'user':
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=f"你收回了：\n{msg}"))

if __name__ == "__main__":
    app.run()
