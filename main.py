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
SETTINGS_FILE = '/data/settings.json'  # 永久存储

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

# Google Translate
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

def load_settings():
    global settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                settings = {int(k): v for k, v in raw.items()}
            print(f"成功加载 settings.json（{len(settings)} 个服务器）")
        except Exception as e:
            print(f"加载 settings.json 失败: {e}")
            settings = {}
    else:
        print("未找到 settings.json，使用空设置")
        settings = {}

def save_settings():
    try:
        os.makedirs('/data', exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        print("settings.json 已保存到 /data")
    except Exception as e:
        print(f"保存 settings.json 失败: {e}")

# 其余函数（clean_title, translate_finance_text, fetch_us_events, format_calendar）全部保持不变
# （为了不刷屏，这里省略，你原来的完全复制进来就行）

# ===================== 定时任务（已修正 lock_file 路径） =====================
@tasks.loop(minutes=1)
async def daily_push():
    await bot.wait_until_ready()

    now_bjt = datetime.datetime.now(BJT)

    if now_bjt.minute % 10 == 0 and now_bjt.second < 10:
        print(f"✅ 心跳正常 - 北京时间 {now_bjt.strftime('%Y-%m-%d %H:%M:%S')} - 已加载 {len(settings)} 个服务器")

    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        lock_file = f"/data/last_push_{today_str}.lock"  # ← 已修正到 /data
        if os.path.exists(lock_file):
            return
        open(lock_file, "w").close()

        print(f"【{now_bjt.strftime('%H:%M')}】开始推送今日经济日历！")

        for guild_id, guild_settings in list(settings.items()):
            try:
                guild = bot.get_guild(guild_id)
                if not guild: continue
                channel = guild.get_channel(guild_settings.get('channel_id'))
                if not channel: continue

                min_imp = guild_settings.get('min_importance', 2)
                events = fetch_us_events(today_str, min_imp)
                embeds = format_calendar(events, today_str, min_imp)

                if embeds:
                    await channel.send(embed=embeds[0])
                    for emb in embeds[1:]:
                        await channel.send(embed=emb)
                    print(f"已推送 → {guild.name}，{len(events)} 条事件")
            except Exception as e:
                print(f"推送失败 guild {guild_id}: {e}")

# 其余 on_ready、命令等全部保持你原来最终版不变

if __name__ == "__main__":
    bot.run(TOKEN)
