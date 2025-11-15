import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz
import json
import os
import re  # for removing parentheses
from google.cloud import translate_v2 as translate  # 需要安装 google-cloud-translate 库

# ================== Configuration ==================
TOKEN = os.getenv('TOKEN') or 'YOUR_BOT_TOKEN_HERE'  # Discord Token
FMP_KEY = os.getenv('FMP_KEY') or 'your_fmp_key_here'  # FMP API Key (Railway Variables)
GOOGLE_TRANSLATE_KEY = os.getenv('GOOGLE_TRANSLATE_API_KEY') or 'your_google_translate_key_here'  # Google Translate API Key
SETTINGS_FILE = 'settings.json'  # Persistent settings file

intents = discord.Intents.default()
intents.message_content = True  # Enable message content
bot = commands.Bot(command_prefix='!', intents=intents)

# Timezones
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')

# FMP Stable API
FMP_URL = "https://financialmodelingprep.com/stable/economic-calendar"

# Speech keywords (English title detection)
SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]

# English weekday mapping
WEEKDAY_MAP = {
    'Monday': 'Monday',
    'Tuesday': 'Tuesday',
    'Wednesday': 'Wednesday',
    'Thursday': 'Thursday',
    'Friday': 'Friday',
    'Saturday': 'Saturday',
    'Sunday': 'Sunday'
}

# Impact mapping (FMP "High" = ★★★, "Medium" = ★★, "Low" = ★)
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

# Impact colors for Embed
IMPACT_COLORS = {"Low": 0x808080, "Medium": 0xFFA500, "High": 0xFF0000}

# Global settings (per-guild, supports multiple servers)
settings = {}  # {guild_id: {'channel_id': int, 'min_importance': 2}}

# 初始化 Google Translate 客户端
translate_client = translate.Client() if GOOGLE_TRANSLATE_KEY != 'your_google_translate_key_here' else None

# 财经术语中英映射字典（确保准确性，基于标准财经翻译，保留英文缩写如 CPI、PPI 等）
FINANCE_TERM_MAP = {
    # 复合术语：保留缩写 + 翻译后缀
    "CPI m/m": "CPI 环比",
    "CPI y/y": "CPI 同比",
    "Core CPI m/m": "核心 CPI 环比",
    "Core CPI y/y": "核心 CPI 同比",
    "PPI m/m": "PPI 环比",
    "PPI y/y": "PPI 同比",
    "GDP Growth Rate q/q": "GDP 增长率 环比（季度）",
    "GDP Growth Rate y/y": "GDP 增长率 同比",
    "Retail Sales m/m": "零售销售 环比",
    "Retail Sales y/y": "零售销售 同比",
    "Industrial Production m/m": "工业生产 环比",
    "ISM Manufacturing PMI": "ISM 制造业 PMI",
    "ISM Services PMI": "ISM 服务业 PMI",
    "Nonfarm Payrolls": "非农就业人数",
    "Unemployment Rate": "失业率",
    "FOMC Meeting Minutes": "FOMC 会议纪要",
    "Fed Interest Rate Decision": "美联储 利率决议",
    "Building Permits": "建筑许可",
    "Housing Starts": "房屋开工",
    "Capacity Utilization": "产能利用率",
    "Consumer Confidence": "消费者信心指数",
    "Michigan Consumer Sentiment": "密歇根 消费者信心指数",
    "Durable Goods Orders": "耐用品订单",
    "Trade Balance": "贸易差额",
    "Current Account": "经常账户",
    "Existing Home Sales": "成屋销售",
    "New Home Sales": "新屋销售",
    "JOLTS Job Openings": "JOLTS 职位空缺",
    "Philly Fed Manufacturing Index": "费城联储 制造业指数",
    "Empire State Manufacturing Index": "帝国州 制造业指数",
    "Leading Index": "领先指标",
    "Beige Book": "褐皮书",

    # 通用后缀/术语
    "m/m": "环比",
    "y/y": "同比",
    "q/q": "环比（季度）",
    "sa": "季节调整",
    "nsa": "非季节调整",
    "Estimate": "预期",
    "Previous": "前值",
    "Actual": "实际值",
    "Forecast": "预测值",

    # 美联储相关
    "FOMC": "FOMC",  # 保留缩写
    "Fed": "美联储",
    "Powell": "鲍威尔",  # 如有姓名，可扩展
}

# 英文缩写列表（保护不翻译）
ENGLISH_ABBREVIATIONS = [
    "CPI", "PPI", "GDP", "ISM", "PMI", "FOMC", "Fed", "JOLTS",
    "Philly Fed", "Empire State", "FRED", "ECB", "BOJ", "BOE"  # 可扩展
]

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

def clean_title(title):
    """Remove parentheses reference period e.g., "CPI m/m (Oct/25)" -> "CPI m/m" """
    title = re.sub(r'\s*\([^)]*\)', '', title).strip()
    return title

def protect_abbreviations(text):
    """保护英文缩写：用临时标记包围，避免翻译"""
    protected = text
    for abbr in ENGLISH_ABBREVIATIONS:
        # 用 {{abbr}} 标记包围缩写（忽略大小写匹配）
        protected = re.sub(rf'\b{re.escape(abbr)}\b', f'{{{{{abbr}}}}}', protected, flags=re.IGNORECASE)
    return protected

def restore_abbreviations(text, original_text):
    """恢复被保护的缩写"""
    restored = text
    for abbr in ENGLISH_ABBREVIATIONS:
        marker = f'{{{{{abbr}}}}}'  # 注意大小写
        # 恢复时匹配原大小写，但简化假设原为大写
        restored = restored.replace(marker, abbr)
    return restored

def translate_finance_text(text, target_lang='zh'):
    """翻译财经文本：优先使用映射字典，后用 Google Translate，确保自然和准确，保留英文缩写"""
    if not text or not translate_client:
        return text

    original_text = text

    # 先保护缩写
    protected_text = protect_abbreviations(text)

    # 再尝试字典映射（精确匹配或部分替换，字典已调整为保留缩写）
    translated = protected_text
    for eng_term, zh_term in FINANCE_TERM_MAP.items():
        if eng_term.lower() in translated.lower():
            translated = re.sub(re.escape(eng_term), zh_term, translated, flags=re.IGNORECASE)

    # 如果仍有英文（非缩写部分），使用 Google Translate 翻译剩余部分（自然语言处理）
    if re.search(r'[a-zA-Z]{2,}', translated) and '{' not in translated:  # 如果还有英文且无保护标记（避免翻译保护部分）
        try:
            # 只翻译非保护部分：拆分句子，翻译无标记的部分
            parts = re.split(r'({{{[^}]+}}})', translated)
            final_parts = []
            for part in parts:
                if part.startswith('{{{') and part.endswith('}}}'):
                    final_parts.append(part)  # 保护标记保持
                elif re.search(r'[a-zA-Z]', part):  # 有英文的部分翻译
                    result = translate_client.translate(part.strip(), target_language=target_lang)
                    translated_part = result['translatedText']
                    # 后处理：保留数字和符号
                    translated_part = re.sub(r'(\d+(?:\.\d+)?%?)', r'\1', translated_part)
                    final_parts.append(translated_part)
                else:
                    final_parts.append(part)
            translated = ''.join(final_parts)
        except Exception as e:
            print(f"Translation error: {e}")
            translated = protected_text  # 回退

    # 恢复缩写
    translated = restore_abbreviations(translated, original_text)

    return translated.strip()

def fetch_us_events(target_date_str, min_importance=2):
    """Fetch US events for the specified date (YYYY-MM-DD format)，并翻译结果"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []  # Invalid date return empty
    params = {
        "from": target_date_str,
        "to": target_date_str,
        "apikey": FMP_KEY
    }
    try:
        response = requests.get(FMP_URL, params=params, timeout=10)
        response.raise_for_status()
        data_json = response.json()
        events = {}
        for item in data_json:
            if item.get("country") != "US": continue  # Only US
            imp_str = item.get("impact", "Low")
            imp_num = IMPACT_MAP.get(imp_str.capitalize(), 1)
            if imp_num < min_importance: continue
            importance = "★" * imp_num

            event_title = clean_title(item.get("event", "").strip())  # Remove reference period
            # 翻译标题（保留缩写）
            translated_title = translate_finance_text(event_title)

            dt_str = item.get("date", "")  # YYYY-MM-DD HH:MM:SS
            if not dt_str:
                continue  # Skip events without date
            time_str = dt_str.split()[-1] if ' ' in dt_str else ""
            date_only = dt_str.split()[0] if ' ' in dt_str else dt_str
            try:
                if time_str:
                    et_dt = ET.localize(datetime.datetime.strptime(f"{date_only} {time_str}", "%Y-%m-%d %H:%M:%S"))
                else:
                    et_dt = ET.localize(datetime.datetime.strptime(date_only, "%Y-%m-%d"))
                bjt_dt = et_dt.astimezone(BJT)
                time_display = f"{bjt_dt.strftime('%H:%M')} ({et_dt.strftime('%H:%M')} ET)"
            except ValueError as ve:
                print(f"Time parsing error: {ve}, dt_str: {dt_str}")
                time_display = f"All Day ({date_only})"

            forecast = item.get("estimate", "") or "—"
            previous = item.get("previous", "") or "—"
            # 翻译预测和前值（通常是数字，但如果有描述则翻译，保留缩写）
            translated_forecast = translate_finance_text(forecast) if forecast != "—" else "—"
            translated_previous = translate_finance_text(previous) if previous != "—" else "—"

            event = {
                "time": time_display,
                "importance": importance,
                "title": translated_title,  # 使用翻译后标题
                "forecast": translated_forecast,  # 翻译后
                "previous": translated_previous,  # 翻译后
                "orig_title": item.get("event", "").strip(),
                "date": dt_str  # For de-duplication sorting
            }
            # De-duplicate: take the latest (by date string, take max)
            key = event_title.lower()  # Title lowercase as key
            if key not in events or dt_str > events[key]['date']:
                events[key] = event
        # Convert to list, sort by time
        event_list = sorted(events.values(), key=lambda x: x["time"])
        return event_list
    except Exception as e:
        print(f"API Error: {e}")
        return []

def split_message(message, max_length=1900):  # Leave 100 char buffer
    """Split long message into multiple messages"""
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
                # Too long single line, force cut
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
        embed = discord.Embed(title="今日新闻", description="日期格式无效 (使用 YYYY-MM-DD)", color=0x00FF00)
        return [embed]
    
    if not events:
        embed = discord.Embed(title="今日新闻", description=f"无事件 (★{'★' * (min_importance-1)} 或以上)", color=0x00FF00)
        return [embed]
    
    embed = discord.Embed(title="今日新闻", color=0x00FF00)
    
    for i, e in enumerate(events, 1):
        is_speech = any(keyword.lower() in e['orig_title'].lower() for keyword in SPEECH_KEYWORDS)
        field_name = f"{e['time']} **{e['title']}**"  # 已翻译标题，缩写保留
        if is_speech:
            field_value = f"\n**影响: {e['importance']}**\n\n\n"
        else:
            field_value = f"\n**影响: {e['importance']}**\n预期: {e['forecast']} | 前值: {e['previous']}\n\n\n"  # 使用翻译后值，并调整标签为中文
        embed.add_field(name=field_name, value=field_value, inline=False)
    
    return [embed]

class SaveChannelView(discord.ui.View):
    """Button view: Confirm save channel"""
    def __init__(self, guild_id: str, channel_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.button(label="Set as Default Channel", style=discord.ButtonStyle.primary)
    async def save_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id not in settings:
            settings[self.guild_id] = {}
        settings[self.guild_id]['channel_id'] = self.channel_id
        save_settings()
        await interaction.response.send_message("Saved as default channel! Future pushes will go here.", ephemeral=True)
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
            print(f"Guild {guild_id} channel not found")
            continue

        embeds = format_calendar(fetch_us_events(tomorrow_str, guild_settings['min_importance']), tomorrow_str, guild_settings['min_importance'])
        for embed in embeds:
            await channel.send(embed=embed)
        print(f"Guild {guild_id} pushed {len(fetch_us_events(tomorrow_str, guild_settings['min_importance']))} events")

@daily_push.before_loop
async def before_push():
    now = datetime.datetime.now(ET)
    next_run = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    wait_seconds = (next_run - now).total_seconds()
    print(f"Waiting {wait_seconds/3600:.1f} hours for ET 00:00 push...")
    await discord.utils.sleep_until(next_run)

@bot.event
async def on_ready():
    load_settings()
    print(f'Bot online: {bot.user}')
    if not daily_push.is_running():
        daily_push.start()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Command sync failed: {e}")

# Slash Commands
@bot.tree.command(name="set_channel", description="Set push channel (current channel)")
async def set_channel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings:
        settings[guild_id] = {}
    settings[guild_id]['channel_id'] = interaction.channel_id
    save_settings()
    await interaction.response.send_message(f"Set push channel to: {interaction.channel.mention}", ephemeral=True)

@bot.tree.command(name="set_importance", description="Set minimum importance (1=★, 2=★★, 3=★★★)")
@discord.app_commands.describe(level="Minimum star level (1-3)")
@discord.app_commands.choices(level=[
    discord.app_commands.Choice(name="★ (All)", value=1),
    discord.app_commands.Choice(name="★★ (Medium-High)", value=2),
    discord.app_commands.Choice(name="★★★ (High)", value=3)
])
async def set_importance(interaction: discord.Interaction, level: discord.app_commands.Choice[int]):
    guild_id = str(interaction.guild_id)
    if guild_id not in settings:
        settings[guild_id] = {}
    settings[guild_id]['min_importance'] = level.value
    save_settings()
    await interaction.response.send_message(f"Set minimum importance to {level.name} (value: {level.value})", ephemeral=True)

@bot.tree.command(name="test_push", description="Manual test push tomorrow's calendar")
async def test_push(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    channel_id = interaction.channel_id  # Default to current channel
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
    embeds = format_calendar(fetch_us_events(tomorrow_str, min_imp), tomorrow_str, min_imp)
    for embed in embeds:
        await channel.send(embed=embed)
    if temp_use:
        view = SaveChannelView(guild_id, channel_id)
        await interaction.response.send_message(f"Temporarily pushed to current channel! {channel.mention}\nSet as default?", view=view, ephemeral=True)
    else:
        await interaction.response.send_message(f"Test push sent to {channel.mention}", ephemeral=True)

@bot.tree.command(name="test_date", description="Test calendar for specific date (YYYY-MM-DD)")
@discord.app_commands.describe(date="Test date (e.g., 2025-11-14)")
async def test_date(interaction: discord.Interaction, date: str):
    guild_id = str(interaction.guild_id)
    channel_id = interaction.channel_id  # Default to current channel
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
        await interaction.response.send_message("Date format error! Use YYYY-MM-DD (e.g., 2025-11-14)", ephemeral=True)
        return
    min_imp = settings.get(guild_id, {}).get('min_importance', 2)
    embeds = format_calendar(fetch_us_events(date, min_imp), date, min_imp)
    for embed in embeds:
        await channel.send(embed=embed)
    if temp_use:
        view = SaveChannelView(guild_id, channel_id)
        await interaction.response.send_message(f"Temporarily pushed to current channel! {channel.mention}\nSet as default?", view=view, ephemeral=True)
    else:
        await interaction.response.send_message(f"Test {date} calendar sent to {channel.mention}", ephemeral=True)

@bot.tree.command(name="disable_push", description="Disable calendar push for this server (delete settings)")
async def disable_push(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    if guild_id in settings:
        del settings[guild_id]
        save_settings()
        await interaction.response.send_message("Disabled calendar push for this server! Use /set_channel to re-enable.", ephemeral=True)
    else:
        await interaction.response.send_message("This server has no push settings to disable.", ephemeral=True)

# Run
if __name__ == "__main__":
    bot.run(TOKEN)
