# main.py
import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz
import json
import os

# ================== 配置区 ==================
TOKEN = os.getenv('TOKEN') or 'YOUR_BOT_TOKEN_HERE'  # Discord Token
FMP_KEY = os.getenv('FMP_KEY') or 'your_fmp_key_here'  # FMP API Key (Railway Variables)
SETTINGS_FILE = 'settings.json'  # 持久化设置文件

intents = discord.Intents.default()
intents.message_content = True  # 启用消息内容
bot = commands.Bot(command_prefix='!', intents=intents)

# 时区
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')

# FMP Stable API
FMP_URL = "https://financialmodelingprep.com/stable/economic-calendar"

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

# 星级映射 (FMP 用 "High" = ★★★, "Medium" = ★★, "Low" = ★)
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

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
    """拉取指定日期的美国事件（YYYY-MM-DD 格式）"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []  # 无效日期返回空
    params = {
        "from": target_date_str,
        "to": target_date_str,
        "apikey": FMP_KEY
    }
    try:
        response = requests.get(FMP_URL, params=params, timeout=10)
        response.raise_for_status()
        data_json = response.json()
        events = []
        for item in data_json:
            if item.get("country") != "US": continue  # 只美国
            imp_str = item.get("impact", "Low")
            imp_num = IMPACT_MAP.get(imp_str.capitalize(), 1)
            if imp_num < min_importance: continue
            importance = "★" * imp_num

            dt_str = item.get("date", "")  # YYYY-MM-DD HH:MM:SS
            time_str = dt_str.split()[-1] if ' ' in dt_str else ""
            date_only = dt_str.split()[0] if ' ' in dt_str else dt_str
            try:
                if time_str:
                    et_dt = ET.localize(datetime.datetime.strptime(f"{date_only} {time_str}", "%Y-%m-%d %H:%M:%S"))
                else:
                    et_dt = ET.localize(datetime.datetime.strptime(date_only, "%Y-%m-%d"))
                bjt_dt = et_dt.astimezone(BJT)
                time_display = f"{et_dt.strftime('%H:%M')} ET ({bjt_dt.strftime('%H:%M')} 北京)"
            except:
                time_display = f"{date_only} 全天 ET (时间转换失败)"

            translated_title = translate_title(item.get("event", "").strip())

            event = {
                "time": time_display,
                "importance": importance,
                "title": translated_title,
                "forecast": item.get("estimate", "") or "—",
                "previous": item.get("previous", "") or "—",
                "orig_title": item.get("event", "").strip()
            }
            events.append(event)
        return sorted(events, key=lambda x: x["time"])
    except Exception as e:
        print(f"API 错误: {e}")
        return []

def split_message(message, max_length=1900):  # 留 100 字符裕度
    """分割长消息为多消息"""
    if len(message) <= max_length:
        return [message]
    parts = []
    current = ""
    lines = message.split('\n')
    for line in lines:
        if len(current + line + '\n') > max_length:
            if current:
                parts.append(current.strip())
                current = line + '\n'
            else:
                # 太长单行，强制切
                parts.append(line[:max_length])
                current = line[max_length:] + '\n'
        else:
            current += line + '\n'
    if current:
        parts.append(current.strip())
    return parts

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
    
    message = "\n".join(lines)
    return split_message(message)  # 返回列表，如果长则拆分

class SaveChannelView(discord.ui.View):
    """按钮视图：确认保存频道"""
    def __init__(self, guild_id: str, channel_id: int):
        super().__init__(timeout=300)  # 5 分钟超时
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.button(label="设为默认频道", style=discord.ButtonStyle.primary)
    async def save_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id not in settings:
            settings[self.guild_id] = {}
        settings[self.guild_id]['channel_id'] = self.channel_id
        save_settings()
        await interaction.response.send_message("已保存为默认频道！下次推送会自动发这里。", ephemeral=True)
        self.stop()

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

        messages = format_calendar(fetch_us_events(tomorrow_str, guild_settings['min_importance']), tomorrow_str, guild_settings['min_importance'])
        for msg in messages:
            await channel.send(msg)
        print(f"Guild {guild_id} 已推送 {len(fetch_us_events(tomorrow_str, guild_settings['min_importance']))} 条事件")

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
    channel_id = interaction.channel_id  # 默认当前频道
    temp_use = False
    if guild_id not in settings or 'channel_id' not in settings[guild_id]:
        temp_use = True
        channel = interaction.channel
    else:
        channel = interaction.guild.get_channel(settings[guild_id]['channel_id'])
        if not channel:
            temp_use = True
            channel = interaction.channel
    min_imp = settings.get(guild_id, {}).get('min_importance', 2)
    tomorrow_str = (datetime.datetime.now(ET).date() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    messages = format_calendar(fetch_us_events(tomorrow_str, min_imp), tomorrow_str, min_imp)
    for msg in messages:
        await channel.send(msg)
    if temp_use:
        view = SaveChannelView(guild_id, channel_id)
        await interaction.response.send_message(f"已临时用当前频道推送！{channel.mention}\n想设为默认吗？", view=view, ephemeral=True)
    else:
        await interaction.response.send_message(f"测试推送已发送到 {channel.mention}", ephemeral=True)

@bot.tree.command(name="test_date", description="测试指定日期的日历 (YYYY-MM-DD)")
@discord.app_commands.describe(date="测试日期 (e.g., 2025-11-14)")
async def test_date(interaction: discord.Interaction, date: str):
    guild_id = str(interaction.guild_id)
    channel_id = interaction.channel_id  # 默认当前频道
    temp_use = False
    if guild_id not in settings or 'channel_id' not in settings[guild_id]:
        temp_use = True
        channel = interaction.channel
    else:
        channel = interaction.guild.get_channel(settings[guild_id]['channel_id'])
        if not channel:
            temp_use = True
            channel = interaction.channel
    if not date or len(date) != 10 or date.count('-') != 2:
        await interaction.response.send_message("日期格式错误！用 YYYY-MM-DD (e.g., 2025-11-14)", ephemeral=True)
        return
    min_imp = settings.get(guild_id, {}).get('min_importance', 2)
    messages = format_calendar(fetch_us_events(date, min_imp), date, min_imp)
    for msg in messages:
        await channel.send(msg)
    if temp_use:
        view = SaveChannelView(guild_id, channel_id)
        await interaction.response.send_message(f"已临时用当前频道推送！{channel.mention}\n想设为默认吗？", view=view, ephemeral=True)
    else:
        await interaction.response.send_message(f"测试 {date} 日历已发送到 {channel.mention}", ephemeral=True)

@bot.tree.command(name="disable_push", description="关闭此服务器的日历推送（删除设置）")
async def disable_push(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id in settings:
        del settings[guild_id]
        save_settings()
        await interaction.response.send_message("已关闭此服务器的日历推送！用 /set_channel 重新设置即可恢复。", ephemeral=True)
    else:
        await interaction.response.send_message("此服务器未设置推送，无需关闭。", ephemeral=True)

# 启动
if __name__ == "__main__":
    bot.run(TOKEN)
