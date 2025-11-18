import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz
import json
import os
import re
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== Configuration ==================
TOKEN = os.getenv('TOKEN') or 'YOUR_BOT_TOKEN_HERE'
FMP_KEY = os.getenv('FMP_KEY') or 'your_fmp_key_here'
SETTINGS_FILE = '/data/settings.json'  # 永久存储路径

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Timezones
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')
UTC = pytz.UTC

FMP_URL = "https://financialmodelingprep.com/stable/economic-calendar"

SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]

WEEKDAY_MAP = {
    'Monday': '周一', 'Tuesday': '周二', 'Wednesday': '周三', 'Thursday': '周四',
    'Friday': '周五', 'Saturday': '周六', 'Sunday': '周日'
}

IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

settings = {}

# Google Translate 初始化（保持不变）
json_key = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
if json_key:
    try:
        credentials = service_account.Credentials.from_service_account_info(json.loads(json_key))
        translate_client = translate.Client(credentials=credentials)
        print('Google Translate SDK 初始化成功')
    except Exception as e:
        print(f'SDK 初始化失败: {e}')
        translate_client = None
else:
    print('未设置 GOOGLE_APPLICATION_CREDENTIALS')
    translate_client = None

# load_settings / save_settings / clean_title / translate_finance_text / fetch_us_events / format_calendar
# （这些函数全部保持你原来最终版不变，这里省略以免太长）

# ===================== 关键：强制同步斜杠命令 =====================
@bot.event
async def on_ready():
    load_settings()
    print(f'Bot 上线: {bot.user}')

    # 启动每日推送任务
    if not daily_push.is_running():
        daily_push.start()

    # 强制同步斜杠命令（解决 CommandNotFound）
    try:
        synced = await bot.tree.sync()
        print(f"已强制同步 {len(synced)} 个斜杠命令（全局）")
    except Exception as e:
        print(f"命令同步失败: {e}")

    # 可选：如果你想更快看到命令（只在特定服务器），可以再加一行（替换 YOUR_GUILD_ID）
    # await bot.tree.sync(guild=discord.Object(id=YOUR_GUILD_ID))

# ===================== 其余代码全部保持不变 =====================
# daily_push、SaveChannelView、set_channel、set_importance、test_push、disable_push
# （直接复制你之前最终版的这些部分）

# 示例 daily_push（记得 lock_file 用 /data）
@tasks.loop(minutes=1)
async def daily_push():
    await bot.wait_until_ready()
    now_bjt = datetime.datetime.now(BJT)
    if now_bjt.minute % 10 == 0 and now_bjt.second < 10:
        print(f"✅ 心跳正常 - 北京时间 {now_bjt.strftime('%Y-%m-%d %H:%M:%S')} - 已加载 {len(settings)} 个服务器")

    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        lock_file = f"/data/last_push_{today_str}.lock"
        if os.path.exists(lock_file):
            return
        open(lock_file, "w").close()
        print(f"【{now_bjt.strftime('%H:%M')}】开始推送今日经济日历！")
        # 推送逻辑保持不变...

if __name__ == "__main__":
    bot.run(TOKEN)
