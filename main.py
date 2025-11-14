# bot.py
import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz

# ================== 配置区 ==================
TOKEN = 'YOUR_BOT_TOKEN_HERE'          # ← 替换你的 Bot Token
CHANNEL_ID = 123456789012345678        # ← 替换你的频道 ID
# ===========================================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

# 时区
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')

# Investing.com API（无需密钥）
API_URL = "https://api.investing.com/api/financialdata/calendar"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
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

def translate_title(title):
    """翻译标题：优先字典匹配，否则返回原标题"""
    # 简单关键词匹配（忽略大小写，部分匹配）
    lower_title = title.lower()
    for eng, chi in TRANSLATION_DICT.items():
        if eng.lower() in lower_title:
            return title.replace(eng, chi, 1)  # 只替换第一个匹配
    return title  # 未匹配保持英文

def fetch_us_events():
    # 第二天日期
    tomorrow = datetime.datetime.now(ET).date() + datetime.timedelta(days=1)
    params = {
        "date": tomorrow.strftime("%Y-%m-%d"),
        "country": "5",        # 美国
        "importance": "2,3"    # ★★ 和 ★★★
    }
    try:
        response = requests.get(API_URL, headers=HEADERS, params=params, timeout=10)
        data = response.json()
        events = []
        for item in data.get("data", []):
            time_str = item["time"].strip()
            if not time_str or time_str == "All Day": continue

            # 解析时间
            try:
                et_time = datetime.datetime.strptime(time_str, "%H:%M").time()
                et_dt = datetime.datetime.combine(tomorrow, et_time, tzinfo=ET)
                bjt_dt = et_dt.astimezone(BJT)
                time_display = f"{et_time.strftime('%H:%M')} ET ({bjt_dt.strftime('%H:%M')} 北京)"
            except:
                time_display = f"{time_str} ET (时间转换失败)"

            importance = "★★★" if item["importance"] == "3" else "★★"

            # 翻译标题
            translated_title = translate_title(item["title"].strip())

            event = {
                "time": time_display,
                "importance": importance,
                "title": translated_title,
                "forecast": item["forecast"] or "—",
                "previous": item["previous"] or "—",
                "orig_title": item["title"].strip()  # 保留原标题用于讲话检测
            }
            events.append(event)
        return sorted(events, key=lambda x: x["time"])
    except Exception as e:
        print(f"API 错误: {e}")
        return []

def format_calendar(events):
    tomorrow = datetime.datetime.now(ET).date() + datetime.timedelta(days=1)
    tomorrow_dt = datetime.datetime.combine(tomorrow, datetime.time(0, 0), tzinfo=ET)
    weekday_en = tomorrow_dt.strftime('%A')
    weekday_cn = WEEKDAY_MAP.get(weekday_en, weekday_en)  # 默认英文如果无映射
    
    if not events:
        return f"**{tomorrow.strftime('%Y-%m-%d')} ({weekday_cn}) 无美国经济事件（★★ 或 ★★★）**"
    
    lines = [f"**{tomorrow.strftime('%Y-%m-%d')} ({weekday_cn}) 美国宏观经济日历**"]
    for e in events:
        # 格式：时间 + 加粗标题 + 星星（后面）
        lines.append(f"\n{e['time']} **{e['title']}** {e['importance']}")
        
        # 检查是否讲话类：不显示预测/前值（用原英文标题判断）
        if not any(keyword.lower() in e['orig_title'].lower() for keyword in SPEECH_KEYWORDS):
            lines.append(f"   预测: {e['forecast']} | 前值: {e['previous']}")
    
    return "\n".join(lines)

@tasks.loop(hours=24)
async def daily_push():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("频道未找到！请检查 CHANNEL_ID")
        return

    events = fetch_us_events()
    message = format_calendar(events)
    await channel.send(message)
    print(f"已推送 {len(events)} 条事件（{datetime.datetime.now(ET).strftime('%Y-%m-%d')} 的明日预告）")

@daily_push.before_loop
async def before_push():
    # 等待到美东时间 00:00
    now = datetime.datetime.now(ET)
    next_run = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    wait_seconds = (next_run - now).total_seconds()
    print(f"等待 {wait_seconds/3600:.1f} 小时后，于美东 00:00 推送明日日历...")
    await discord.utils.sleep_until(next_run)

@bot.event
async def on_ready():
    print(f'Bot 已上线: {bot.user}')
    if not daily_push.is_running():
        daily_push.start()

# 启动
bot.run(TOKEN)