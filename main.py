# main.py
import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz
import json
import os

# ================== 配置区 ==================
TOKEN = os.getenv('TOKEN') or 'YOUR_BOT_TOKEN_HERE'  # 从环境变量读取，Railway 设置
SETTINGS_FILE = 'settings.json'  # 持久化设置文件

intents = discord.Intents.default()
intents.message_content = True  # 启用消息内容
bot = commands.Bot(command_prefix='!', intents=intents)

# 时区
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')

# Investing.com API（POST 端点）
API_URL = "https://www.investing.com/economic-calendar/Service/getCalendarFilteredData"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
    "Referer": "https://www.investing.com/economic-calendar/",
    "Origin": "https://www.investing.com"
}

# 讲话类关键词（英文标题检测）
SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]

# 中英对照字典（常见美国宏观事件翻译）
TRANSLATION_DICT = {
    # 数据类
    "CPI m/m": "消费者物价指数月率",
    "Core CPI m/m": "核心消费者物价指数月率",
    "CPI y/y": "消费者物价指数年率",
    "Core CPI y/y": "核心消费者物价指数年率",
    "Nonfarm Payrolls": "非农就业人数",
    "Unemployment Rate": "失业率",
    "Retail Sales m/m": "零售销售月率",
    "Core Retail Sales m/m": "核心零售销售月率",
    "PPI m/m": "生产者物价指数月率",
    "Core PPI m/m": "核心生产者物价指数月率",
    "ISM Manufacturing PMI": "ISM制造业PMI",
    "ISM Services PMI": "ISM服务业PMI",
    "Industrial Production m/m": "工业生产月率",
    "Capacity Utilization": "产能利用率",
    "Housing Starts": "房屋开工",
    "Building Permits": "建筑许可",
    "Existing Home Sales": "成屋销售",
    "Michigan Consumer Sentiment": "密歇根大学消费者信心指数",
    "GDP Growth Rate q/q": "GDP季率初值",
    "Durable Goods Orders": "耐用品订单月率",
    "Initial Jobless Claims": "初请失业金人数",
    "Continuing Jobless Claims": "续请失业金人数",
    "Trade Balance": "贸易差额",
    "Current Account": "经常账户",
    "FOMC Rate Decision": "FOMC利率决定",
    "Fed Funds Rate": "联邦基金利率",
    
    # 讲话类
    "Fed Chair Powell Speech": "美联储主席鲍威尔讲话",
    "Fed Chair Powell Testimony": "美联储主席鲍威尔证词",
    "FOMC Press Conference": "FOMC新闻发布会",
    "Fed Governor Speech": "美联储理事讲话",
    "Fed Vice Chair Speech": "美联储副主席讲话",
    "FOMC Minutes": "FOMC会议纪要",
    
    # 其他
    "Consumer Confidence": "消费者信心指数",
    "JOLTs Job Openings": "JOLTs职位空缺",
    "Factory Orders": "工厂订单月率",
    "ISM Non-Manufacturing PMI": "ISM非制造业PMI"
}

# 中文星期映射（简写版）
WEEKDAY_MAP = {
    'Monday': '周一',
    'Tuesday': '周二',
    'Wednesday': '周三',
    'Thursday': '周四',
    'Friday': '周五',
    'Saturday': '周六',
    'Sunday': '周日'
}

# 全局设置（per-guild，支持多服务器）
settings = {}  # {guild_id: {'channel_id': int, 'min_importance': 2}}

def load_settings():
    global settings
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
    else:
        settings = {}

def save_settings():
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def translate_title(title):
    """翻译标题：优先字典匹配，否则返回原标题"""
    lower_title = title.lower()
    for eng, chi in TRANSLATION_DICT.items():
        if eng.lower() in lower_title:
            return title.replace(eng, chi, 1)
    return title

def fetch_us_events(target_date_str, min_importance=2):
    """拉取指定日期的事件（YYYY-MM-DD 格式）"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []  # 无效日期返回空
    importance_str = ",".join([str(i) for i in range(min_importance, 4)])  # e.g., "2,3"
    data = {
        'dateFrom': target_date_str,
        'dateTo': target_date_str,
        'timeZone': '8',  # GMT+8 for Beijing-compatible
        'country': '5',  # 美国
        'importance': importance_str,
        'limit_from': '0'  # 第一页
    }
    try:
        response = requests.post(API_URL, headers=HEADERS, data=data, timeout=10)
        response.raise_for_status()  # 检查 HTTP 错误
        data_json = response.json()
        events = []
        for item in data_json.get("data", []):
            time_str = item.get("date", "").strip()
            if not time_str or time_str == "All Day": continue

            try:
                # 解析时间 (格式如 "2025-11-14 08:30")
                if len(time_str.split()) > 1:
                    dt_str = f"{target_date_str} {time_str.split()[1]}"
                    et_dt = ET.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M"))
                    bjt_dt = et_dt.astimezone(BJT)
                    time_display = f"{et_dt.strftime('%H:%M')} ET ({bjt_dt.strftime('%H:%M')} 北京)"
                else:
                    time_display = f"{time_str} ET (时间转换失败)"
            except:
                time_display = f"{time_str} ET (时间转换失败)"

            imp_num = int(item.get("impact", 1))
            if imp_num < min_importance: continue
            importance = "★" * imp_num

            translated_title = translate_title(item.get("name", "").strip())

            event = {
                "time": time_display,
                "importance": importance,
                "title": translated_title,
                "forecast": item.get("forecast", "") or "—",
                "previous": item.get("previous", "") or "—",
                "orig_title": item.get("name", "").strip()
            }
            events.append(event)
        return sorted(events, key=lambda x: x["time"])
    except json.JSONDecodeError as e:
        print(f"JSON 解析错误: {e} (响应: {response.text[:100]})")
        return []
    except Exception as e:
        print(f"API 错误: {e}")
        return []

def format_calendar(events, target_date_str, min_importance):
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return "**无效日期格式 (用 YYYY-MM-DD)**"
    target_dt = datetime.datetime.combine(target_date, datetime.time(0, 0), tzinfo=ET)
    weekday_en = target_dt.strftime('%A')
    weekday_cn = WEEKDAY_MAP.get(weekday_en, weekday_en)
    
    if not events:
        return f"**{target_date_str} ({weekday_cn}) 无美国经济事件（{min_importance}★ 或以上）**"
    
    lines = [f"**{target_date_str} ({weekday_cn}) 美国宏观经济日历**"]
    for e in events:
        lines.append(f"\n{e['time']} **{e['title']}** {e['importance']}")
        
        if not any(keyword.lower() in e['orig_title'].lower() for keyword in SPEECH_KEYWORDS):
            lines.append(f"   预测: {e['forecast']} | 前值: {e['previous']}")
    
    return "\n".join(lines)

@tasks.loop(hours=24)
async def daily_push():
    await bot.wait_until_ready()
    tomorrow_str = (datetime.datetime.now(ET).date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    for guild_id, guild_settings in settings.items():
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        channel = guild.get_channel(guild_settings['channel_id'])
        if not channel:
            print(f"Guild {guild_id} 频道未找到")
            continue

        events = fetch_us_events(tomorrow_str, guild_settings['min_importance'])
        message = format_calendar(events, tomorrow_str, guild_settings['min_importance'])
        await channel.send(message)
        print(f"Guild {guild_id} 已推送 {len(events)} 条事件")

@daily_push.before_loop
async def before_push():
    now = datetime.datetime.now(ET)
    next_run = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    wait_seconds = (next_run - now).total_seconds()
    print(f"等待 {wait_seconds/3600:.1f} 小时后，于美东 00:00 推送...")
    await discord.utils.sleep_until(next_run)

@bot.event
async def on_ready():
    load_settings()
    print(f'Bot 已上线: {bot.user}')
    if not daily_push.is_running():
        daily_push.start()
    try:
        synced = await bot.tree.sync()
        print(f"已同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        print(f"同步命令失败: {e}")

# 斜杠命令组
@bot.tree.command(name="set_channel", description="设置推送频道（当前频道）")
async def set_channel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings:
        settings[guild_id] = {}
    settings[guild_id]['channel_id'] = interaction.channel_id
    save_settings()
    await interaction.response.send_message(f"已设置推送频道为: {interaction.channel.mention}", ephemeral=True)

@bot.tree.command(name="set_importance", description="设置最小重要性 (1=★, 2=★★, 3=★★★)")
@discord.app_commands.describe(level="最小星级 (1-3)")
@discord.app_commands.choices(level=[
    discord.app_commands.Choice(name="★ (所有)", value=1),
    discord.app_commands.Choice(name="★★ (中高)", value=2),
    discord.app_commands.Choice(name="★★★ (高)", value=3)
])
async def set_importance(interaction: discord.Interaction, level: discord.app_commands.Choice[int]):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings:
        settings[guild_id] = {}
    settings[guild_id]['min_importance'] = level.value
    save_settings()
    await interaction.response.send_message(f"已设置最小重要性为 {level.name} (数值: {level.value})", ephemeral=True)

@bot.tree.command(name="test_push", description="手动测试推送明日日历")
async def test_push(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings or 'channel_id' not in settings[guild_id]:
        await interaction.response.send_message("请先用 /set_channel 设置频道", ephemeral=True)
        return
    channel = interaction.guild.get_channel(settings[guild_id]['channel_id'])
    min_imp = settings[guild_id].get('min_importance', 2)
    tomorrow_str = (datetime.datetime.now(ET).date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    events = fetch_us_events(tomorrow_str, min_imp)
    message = format_calendar(events, tomorrow_str, min_imp)
    await channel.send(message)
    await interaction.response.send_message(f"测试推送已发送到 {channel.mention}", ephemeral=True)

@bot.tree.command(name="test_date", description="测试指定日期的日历 (YYYY-MM-DD)")
@discord.app_commands.describe(date="测试日期 (e.g., 2025-11-14)")
async def test_date(interaction: discord.Interaction, date: str):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings or 'channel_id' not in settings[guild_id]:
        await interaction.response.send_message("请先用 /set_channel 设置频道", ephemeral=True)
        return
    if not date or len(date) != 10 or date.count('-') != 2:
        await interaction.response.send_message("日期格式错误！用 YYYY-MM-DD (e.g., 2025-11-14)", ephemeral=True)
        return
    channel = interaction.guild.get_channel(settings[guild_id]['channel_id'])
    min_imp = settings[guild_id].get('min_importance', 2)
    events = fetch_us_events(date, min_imp)
    message = format_calendar(events, date, min_imp)
    await channel.send(message)
    await interaction.response.send_message(f"测试 {date} 日历已发送到 {channel.mention}", ephemeral=True)

# 启动
if __name__ == "__main__":
    bot.run(TOKEN)
