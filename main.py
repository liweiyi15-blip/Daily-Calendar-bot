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

# ================== Google Translate ==================
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

# ================== 永久存储函数 ==================
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

# ================== 工具函数 ==================
def clean_title(title):
    if not isinstance(title, str):
        title = str(title)
    return re.sub(r'\s*\([^)]*\)', '', title).strip()

def translate_finance_text(text, target_lang='zh'):
    if not text or not translate_client:
        return str(text).strip()
    text = str(text).strip()
    if re.match(r'^-?\d+(\.\d+)?%?$', text):
        return text
    try:
        if translate_client.detect_language(text)['language'].startswith('zh'):
            return text
        result = translate_client.translate(text, source_language='en', target_language=target_lang)
        translated = result['translatedText']
        for abbr in ['CPI', 'PPI', 'GDP', 'ISM', 'PMI', 'FOMC', 'Fed', 'JOLTS', 'CFTC', 'S&P', 'QoQ', 'MoM', 'YoY']:
            translated = re.sub(rf'\b{abbr}\b', abbr, translated, flags=re.IGNORECASE)
        return translated.strip()
    except Exception as e:
        print(f'翻译异常: {e}')
        return text.strip()

def fetch_us_events(target_date_str, min_importance=2):
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []

    params = {"from": target_date_str, "to": target_date_str, "apikey": FMP_KEY}
    try:
        resp = requests.get(FMP_URL, params=params, timeout=10)
        resp.raise_for_status()
        data_json = resp.json()

        events = {}
        start_bjt = BJT.localize(datetime.datetime.combine(target_date, datetime.time(8, 0)))
        end_bjt = start_bjt + datetime.timedelta(days=1)

        for item in data_json:
            if item.get("country") != "US":
                continue
            imp_num = IMPACT_MAP.get(item.get("impact", "Low").capitalize(), 1)
            if imp_num < min_importance:
                continue

            dt_str = item.get("date")
            if not dt_str:
                continue
            utc_dt = UTC.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"))
            bjt_dt = utc_dt.astimezone(BJT)
            if not (start_bjt <= bjt_dt < end_bjt):
                continue

            et_dt = utc_dt.astimezone(ET)
            time_display = f"{bjt_dt.strftime('%H:%M')} ({et_dt.strftime('%H:%M')} ET)"

            raw_title = item.get("event", "")
            title = clean_title(raw_title)
            translated_title = translate_finance_text(title)

            forecast = translate_finance_text(item.get("estimate", "") or "—") if item.get("estimate") not in ["", None] else "—"
            previous = translate_finance_text(item.get("previous", "") or "—") if item.get("previous") not in ["", None] else "—"

            event = {
                "time": time_display,
                "importance": "★" * imp_num,
                "title": translated_title,
                "forecast": forecast,
                "previous": previous,
                "orig_title": raw_title,
                "bjt_timestamp": bjt_dt
            }
            key = title.lower()
            if key not in events or dt_str > events[key].get("date", ""):
                event["date"] = dt_str
                events[key] = event

        return sorted(events.values(), key=lambda x: x["bjt_timestamp"])
    except Exception as e:
        print(f"FMP API 错误: {e}")
        return []

def format_calendar(events, target_date_str, min_importance):
    now_bjt = datetime.datetime.now(BJT)
    date_str = now_bjt.strftime('%m月%d日')
    weekday_en = now_bjt.strftime('%A')
    weekday_cn = WEEKDAY_MAP.get(weekday_en, '未知')
    title = f"今日热点（{date_str}/{weekday_cn}）"

    if not events:
        embed = discord.Embed(title=title, description=f"无事件 (★{'★'*(min_importance-1)} 或以上)", color=0x00FF00)
        return [embed]

    embed = discord.Embed(title=title, color=0x00FF00)
    for e in events:
        is_speech = any(kw.lower() in e['orig_title'].lower() for kw in SPEECH_KEYWORDS)
        field_name = f"{e['time']} **{e['title']}**"
        if is_speech:
            field_value = f"**影响: {e['importance']}**"
        else:
            field_value = f"**影响: {e['importance']}**\n预期: {e['forecast']} | 前值: {e['previous']}"
        embed.add_field(name=field_name, value=field_value, inline=False)
    return [embed]

# ================== 按钮视图 ==================
class SaveChannelView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.button(label="设为默认频道", style=discord.ButtonStyle.primary)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id not in settings:
            settings[self.guild_id] = {}
        settings[self.guild_id]['channel_id'] = self.channel_id
        settings[self.guild_id]['min_importance'] = settings[self.guild_id].get('min_importance', 2)
        save_settings()
        await interaction.response.send_message("已成功设为默认推送频道！", ephemeral=True)
        self.stop()

# ================== 定时任务（强制每分钟打印心跳） ==================
@tasks.loop(minutes=1)
async def daily_push():
    await bot.wait_until_ready()

    now_bjt = datetime.datetime.now(BJT)

    # 【强制每分钟打印一次心跳】确认任务在跑，看到了就改回每10分钟也行
    print(f"✅ 心跳正常 - 北京时间 {now_bjt.strftime('%Y-%m-%d %H:%M:%S')} - 已加载 {len(settings)} 个服务器")

    # 每天北京时间 08:00~08:04 推送一次
    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        lock_file = f"/data/last_push_{today_str}.lock"
        if os.path.exists(lock_file):
            return
        open(lock_file, "w").close()

        print(f"【{now_bjt.strftime('%H:%M')}】开始推送今日经济日历！")

        for guild_id, guild_settings in list(settings.items()):
            try:
                guild = bot.get_guild(guild_id)
                if not guild:
                    continue
                channel = guild.get_channel(guild_settings.get('channel_id'))
                if not channel:
                    continue

                min_imp = guild_settings.get('min_importance', 2)
                events = fetch_us_events(today_str, min_imp)
                embeds = format_calendar(events, today_str, min_imp)

                if embeds:
                    await channel.send(embed=embeds[0])
                    for emb in embeds[1:]:
                        await channel.send(embed=emb)
                    print(f"已推送 → {guild.name} ({guild_id})，{len(events)} 条事件")
            except Exception as e:
                print(f"推送失败 guild {guild_id}: {e}")

# ================== on_ready（三保险启动 + 强制同步） ==================
@bot.event
async def on_ready():
    load_settings()
    print(f'Bot 上线: {bot.user}')

    # 强制同步斜杠命令
    try:
        synced = await bot.tree.sync()
        print(f"已强制同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        print(f"命令同步失败: {e}")

    # 三保险启动 daily_push
    if not daily_push.is_running():
        daily_push.start()
        print("daily_push 任务已启动")
    else:
        print("daily_push 已经在运行")

    # 保底 5 秒后再检查一次
    async def ensure_task():
        await discord.utils.sleep_until(datetime.datetime.now(UTC) + datetime.timedelta(seconds=5))
        if not daily_push.is_running():
            daily_push.start()
            print("【保底】daily_push 已启动")
    bot.loop.create_task(ensure_task())

# ================== 斜杠命令 ==================
@bot.tree.command(name="set_channel", description="设置推送频道（当前频道）")
async def set_channel(interaction: discord.Interaction):
    gid = interaction.guild_id
    settings[gid] = settings.get(gid, {})
    settings[gid]['channel_id'] = interaction.channel_id
    settings[gid]['min_importance'] = settings[gid].get('min_importance', 2)
    save_settings()
    await interaction.response.send_message(f"推送频道已设为 {interaction.channel.mention}", ephemeral=True)

@bot.tree.command(name="set_importance", description="设置最低重要程度")
@discord.app_commands.choices(level=[
    discord.app_commands.Choice(name="★ (全部)", value=1),
    discord.app_commands.Choice(name="★★ (中高)", value=2),
    discord.app_commands.Choice(name="★★★ (高)", value=3),
])
async def set_importance(interaction: discord.Interaction, level: discord.app_commands.Choice[int]):
    gid = interaction.guild_id
    settings[gid] = settings.get(gid, {})
    settings[gid]['min_importance'] = level.value
    save_settings()
    await interaction.response.send_message(f"最低重要程度设为 {level.name}", ephemeral=True)

@bot.tree.command(name="test_push", description="手动测试今日日历")
async def test_push(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    gid = interaction.guild_id
    min_imp = settings.get(gid, {}).get('min_importance', 2)
    today = datetime.datetime.now(BJT).strftime("%Y-%m-%d")
    
    target_channel = interaction.channel
    need_save_view = False
    
    if gid in settings and settings[gid].get('channel_id'):
        saved_channel = interaction.guild.get_channel(settings[gid]['channel_id'])
        if saved_channel:
            target_channel = saved_channel
        else:
            need_save_view = True
    else:
        need_save_view = True

    events = fetch_us_events(today, min_imp)
    embeds = format_calendar(events, today, min_imp)

    if embeds:
        await target_channel.send(embed=embeds[0])
        for emb in embeds[1:]:
            await target_channel.send(embed=emb)

    if need_save_view:
        view = SaveChannelView(gid, interaction.channel_id)
        await interaction.followup.send("已推送到当前频道，要设为默认推送频道吗？", view=view, ephemeral=True)
    else:
        await interaction.followup.send(f"推送完成，已发送到默认频道 {target_channel.mention}", ephemeral=True)

@bot.tree.command(name="disable_push", description="关闭本服务器推送")
async def disable_push(interaction: discord.Interaction):
    gid = interaction.guild_id
    if gid in settings:
        del settings[gid]
        save_settings()
        await interaction.response.send_message("已关闭本服务器推送", ephemeral=True)
    else:
        await interaction.response.send_message("本服务器没有开启推送", ephemeral=True)

# ================== 启动 ==================
if __name__ == "__main__":
    bot.run(TOKEN)
