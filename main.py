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
UTC = pytz.UTC

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
    if not isinstance(title, str):
        title = str(title)
    title = re.sub(r'\s*\([^)]*\)', '', title).strip()
    return title

def translate_finance_text(text, target_lang='zh'):
    """翻译财经文本：完全使用 Google Translate，确保自然和准确，保留数字和符号"""
    if not text or not translate_client:
        return str(text).strip()

    text = str(text)
    try:
        result = translate_client.translate(text, target_language=target_lang)
        translated = result['translatedText']
        # 后处理：保留数字和符号（如百分比、缩写不改）
        translated = re.sub(r'(\d+(?:\.\d+)?%?)', r'\1', translated)
        # 保留常见缩写（如 CPI, PPI）不翻译
        for abbr in ['CPI', 'PPI', 'GDP', 'ISM', 'PMI', 'FOMC', 'Fed', 'JOLTS', 'CFTC', 'S&P', 'QoQ', 'MoM', 'YoY']:
            translated = re.sub(rf'\b{re.escape(abbr)}\b', abbr, translated, flags=re.IGNORECASE)
        return translated.strip()
    except Exception as e:
        print(f"Translation error: {e}")
        return text.strip()

def fetch_us_events(target_date_str, min_importance=2):
    """Fetch US events for the specified date (YYYY-MM-DD format)，并翻译结果。FMP date 是 UTC 时间。只保留 BJT 08:00 到次日 08:00 的事件"""
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
        print(f"FMP API returned {len(data_json)} events for {target_date_str} (UTC full day)")  # 日志确认范围
        events = {}
        # 计算 BJT 范围：target_date 08:00 到次日 08:00
        start_bjt = BJT.localize(datetime.datetime.combine(target_date, datetime.time(8, 0)))
        end_bjt = start_bjt + datetime.timedelta(days=1)
        filtered_count = 0
        for item in data_json:
            if item.get("country") != "US": continue  # Only US
            imp_str = item.get("impact", "Low")
            imp_num = IMPACT_MAP.get(imp_str.capitalize(), 1)
            if imp_num < min_importance: continue
            importance = "★" * imp_num

            dt_str = item.get("date", "")  # YYYY-MM-DD HH:MM:SS UTC
            if not dt_str:
                continue  # Skip events without date
            try:
                # 正确解析：dt_str 是 UTC 时间
                utc_dt = UTC.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"))
                bjt_dt = utc_dt.astimezone(BJT)
                # 过滤：只保留 BJT 08:00 到次日 08:00
                if not (start_bjt <= bjt_dt < end_bjt):
                    continue
                filtered_count += 1
                et_dt = utc_dt.astimezone(ET)
                time_display = f"{bjt_dt.strftime('%H:%M')} ({et_dt.strftime('%H:%M')} ET)"
            except ValueError as ve:
                print(f"Time parsing error: {ve}, dt_str: {dt_str}")
                continue

            event_title = clean_title(item.get("event", ""))  # Remove reference period
            # 翻译标题（完全使用 Google）
            translated_title = translate_finance_text(event_title)
            print(f"Translated '{item.get('event')} -> {translated_title}'")  # 日志调试翻译

            forecast = item.get("estimate", "") or "—"
            previous = item.get("previous", "") or "—"
            # 翻译预测和前值（通常是数字，但如果有描述则翻译）
            translated_forecast = translate_finance_text(forecast) if forecast != "—" else "—"
            translated_previous = translate_finance_text(previous) if previous != "—" else "—"

            event = {
                "time": time_display,
                "importance": importance,
                "title": translated_title,  # 使用翻译后标题
                "forecast": translated_forecast,  # 翻译后
                "previous": translated_previous,  # 翻译后
                "orig_title": item.get("event", ""),
                "date": dt_str,  # For de-duplication sorting
                "bjt_timestamp": bjt_dt  # 新增：用于 chronological 排序
            }
            # De-duplicate: take the latest (by date string, take max)
            key = event_title.lower()  # Title lowercase as key
            if key not in events or dt_str > events[key]['date']:
                events[key] = event
        # Convert to list, sort by bjt_timestamp (chronological, handles cross-midnight)
        event_list = sorted(events.values(), key=lambda x: x["bjt_timestamp"])
        print(f"Filtered {filtered_count} events in BJT 08:00 - next 08:00 range")  # 日志过滤计数
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
        embed = discord.Embed(title="经济日历", description="日期格式无效 (使用 YYYY-MM-DD)", color=0x00FF00)
        return [embed]
    
    if not events:
        embed = discord.Embed(title="经济日历", description=f"无事件 (★{'★' * (min_importance-1)} 或以上)", color=0x00FF00)
        return [embed]
    
    # 标题调整为覆盖24h范围
    start_bjt = BJT.localize(datetime.datetime.combine(target_date, datetime.time(8, 0)))
    end_bjt = start_bjt + datetime.timedelta(days=1)
    title = f"经济日历 (BJT {start_bjt.strftime('%m/%d %H:%M')} - {end_bjt.strftime('%m/%d %H:%M')})"
    
    embed = discord.Embed(title=title, color=0x00FF00)
    
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
    # 使用 BJT 当前日期作为 target_date_str（对应 UTC 当天全天，即 BJT 08:00 到次日 08:00 的 US 事件）
    target_date_str = datetime.datetime.now(BJT).date().strftime("%Y-%m-%d")
    for guild_id, guild_settings in settings.items():
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        channel = guild.get_channel(guild_settings['channel_id'])
        if not channel:
            print(f"Guild {guild_id} channel not found")
            continue

        embeds = format_calendar(fetch_us_events(target_date_str, guild_settings['min_importance']), target_date_str, guild_settings['min_importance'])
        for embed in embeds:
            await channel.send(embed=embed)
        print(f"Guild {guild_id} pushed {len(fetch_us_events(target_date_str, guild_settings['min_importance']))} events for {target_date_str}")

@daily_push.before_loop
async def before_push():
    now = datetime.datetime.now(BJT)
    next_run = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += datetime.timedelta(days=1)
    wait_seconds = (next_run - now).total_seconds()
    print(f"Waiting {wait_seconds/3600:.1f} hours for BJT 08:00 push...")
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

@bot.tree.command(name="test_push", description="Manual test push today's calendar (BJT 08:00 to next day)")
async def test_push(interaction: discord.Interaction):
    # 修复：立即 defer 响应，避免 3 秒超时
    await interaction.response.defer(ephemeral=True)
    
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
    target_date_str = datetime.datetime.now(BJT).date().strftime("%Y-%m-%d")
    embeds = format_calendar(fetch_us_events(target_date_str, min_imp), target_date_str, min_imp)
    for embed in embeds:
        await channel.send(embed=embed)
    if temp_use:
        await interaction.followup.send(f"Temporarily pushed to current channel! {channel.mention}\nSet as default?", view=SaveChannelView(guild_id, channel_id), ephemeral=True)
    else:
        await interaction.followup.send(f"Test push sent to {channel.mention}", ephemeral=True)

@bot.tree.command(name="test_date", description="Test calendar for specific date (YYYY-MM-DD)")
@discord.app_commands.describe(date="Test date (e.g., 2025-11-14)")
async def test_date(interaction: discord.Interaction, date: str):
    # 修复：立即 defer 响应，避免 3 秒超时
    await interaction.response.defer(ephemeral=True)
    
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
        await interaction.followup.send("Date format error! Use YYYY-MM-DD (e.g., 2025-11-14)", ephemeral=True)
        return
    min_imp = settings.get(guild_id, {}).get('min_importance', 2)
    embeds = format_calendar(fetch_us_events(date, min_imp), date, min_imp)
    for embed in embeds:
        await channel.send(embed=embed)
    if temp_use:
        await interaction.followup.send(f"Temporarily pushed to current channel! {channel.mention}\nSet as default?", view=SaveChannelView(guild_id, channel_id), ephemeral=True)
    else:
        await interaction.followup.send(f"Test {date} calendar sent to {channel.mention}", ephemeral=True)

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
