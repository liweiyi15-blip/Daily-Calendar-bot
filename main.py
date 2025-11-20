import discord
from discord.ext import commands, tasks
import aiohttp
import datetime
import pytz
import json
import os
import re
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== Configuration ==================
TOKEN = os.getenv('TOKEN')
FMP_KEY = os.getenv('FMP_KEY')
# Railway 建议: 创建一个 Volume 挂载到 /data，否则重启后设置会丢失
SETTINGS_FILE = '/data/settings.json' 

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
translate_client = None

# ================== 初始化 Google Translate ==================
# 优先读取直接存入环境变量的 JSON 字符串 (适配 Railway)
google_json_str = os.getenv('GOOGLE_JSON_CONTENT') 
# 其次读取文件路径
google_key_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

try:
    if google_json_str:
        cred_info = json.loads(google_json_str)
        credentials = service_account.Credentials.from_service_account_info(cred_info)
        translate_client = translate.Client(credentials=credentials)
        print('✅ Google Translate SDK (Env String) 初始化成功')
    elif google_key_path and os.path.exists(google_key_path):
        credentials = service_account.Credentials.from_service_account_file(google_key_path)
        translate_client = translate.Client(credentials=credentials)
        print('✅ Google Translate SDK (File Path) 初始化成功')
    else:
        print('⚠️ 未检测到 Google 凭证，翻译功能将不可用')
except Exception as e:
    print(f'❌ SDK 初始化失败: {e}')

# ================== 永久存储函数 ==================
def load_settings():
    global settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                settings = {int(k): v for k, v in raw.items()}
            print(f"已加载设置: {len(settings)} 个服务器")
        except Exception as e:
            print(f"加载设置失败: {e}")
            settings = {}
    else:
        settings = {}

def save_settings():
    try:
        os.makedirs('/data', exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        print("设置已保存")
    except Exception as e:
        print(f"保存设置失败: {e}")

# ================== 工具函数 ==================
def clean_title(title):
    return re.sub(r'\s*\([^)]*\)', '', str(title)).strip()

def translate_finance_text(text, target_lang='zh'):
    if not text or not translate_client:
        return str(text).strip()
    text = str(text).strip()
    # 纯数字/百分比不翻译
    if re.match(r'^-?\d+(\.\d+)?%?$', text):
        return text
    try:
        # 简单检测，如果已经是中文则跳过
        # 注意: detect_language 也会消耗 API 配额，可视情况移除
        if translate_client.detect_language(text)['language'].startswith('zh'):
            return text
        
        result = translate_client.translate(text, source_language='en', target_language=target_lang)
        translated = result['translatedText']
        # 保留常见金融术语
        for abbr in ['CPI', 'PPI', 'GDP', 'ISM', 'PMI', 'FOMC', 'Fed', 'JOLTS', 'CFTC', 'S&P', 'QoQ', 'MoM', 'YoY']:
            translated = re.sub(rf'\b{abbr}\b', abbr, translated, flags=re.IGNORECASE)
        return translated.strip()
    except Exception as e:
        print(f'翻译异常: {e}')
        return text

# [修改] 改为异步函数，使用 aiohttp
async def fetch_us_events(target_date_str, min_importance=2):
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError:
        return []

    params = {"from": target_date_str, "to": target_date_str, "apikey": FMP_KEY}
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FMP_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data_json = await resp.json()

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
            if not dt_str: continue
            
            utc_dt = UTC.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"))
            bjt_dt = utc_dt.astimezone(BJT)
            
            if not (start_bjt <= bjt_dt < end_bjt):
                continue

            et_dt = utc_dt.astimezone(ET)
            time_display = f"{bjt_dt.strftime('%H:%M')} ({et_dt.strftime('%H:%M')} ET)"

            raw_title = item.get("event", "")
            title = clean_title(raw_title)
            # 注意：这里Google翻译依然是同步调用，若由于并发量大卡顿，需用 run_in_executor 优化
            translated_title = translate_finance_text(title)
            
            forecast = translate_finance_text(item.get("estimate", "") or "—")
            previous = translate_finance_text(item.get("previous", "") or "—")

            event = {
                "time": time_display,
                "importance": "★" * imp_num,
                "title": translated_title,
                "forecast": forecast,
                "previous": previous,
                "orig_title": raw_title,
                "bjt_timestamp": bjt_dt,
                "date": dt_str
            }
            key = title.lower()
            if key not in events or dt_str > events[key].get("date", ""):
                events[key] = event

        return sorted(events.values(), key=lambda x: x["bjt_timestamp"])
    except Exception as e:
        print(f"FMP API 错误: {e}")
        return []

def format_calendar(events, target_date_str, min_importance):
    now_bjt = datetime.datetime.now(BJT)
    date_str = now_bjt.strftime('%m月%d日')
    weekday_cn = WEEKDAY_MAP.get(now_bjt.strftime('%A'), '未知')
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
        await interaction.response.send_message("✅ 已成功设为默认推送频道！", ephemeral=True)
        self.stop()

# ================== 定时任务 ==================
@tasks.loop(minutes=1)
async def daily_push():
    now_bjt = datetime.datetime.now(BJT)
    # 这里的 print 会显示在 Railway 的 Logs 里
    print(f"💓 心跳 - {now_bjt.strftime('%H:%M')}")

    # 每天北京时间 08:00 - 08:05 之间触发
    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        # 确保 /data 目录存在
        os.makedirs('/data', exist_ok=True)
        lock_file = f"/data/last_push_{today_str}.lock"
        
        if os.path.exists(lock_file):
            return
        
        # 创建锁文件
        with open(lock_file, "w") as f:
            f.write("locked")

        print(f"🚀 开始推送 {today_str} 简报...")

        for guild_id, guild_settings in list(settings.items()):
            try:
                guild = bot.get_guild(guild_id)
                channel_id = guild_settings.get('channel_id')
                if not guild or not channel_id: continue
                
                channel = guild.get_channel(channel_id)
                if not channel: continue

                min_imp = guild_settings.get('min_importance', 2)
                # 使用 await 调用异步函数
                events = await fetch_us_events(today_str, min_imp)
                embeds = format_calendar(events, today_str, min_imp)

                if embeds:
                    await channel.send(embed=embeds[0])
                    for emb in embeds[1:]:
                        await channel.send(embed=emb)
                    print(f"已推送 -> {guild.name}")
            except Exception as e:
                print(f"推送失败 {guild_id}: {e}")

@daily_push.before_loop
async def before_push():
    await bot.wait_until_ready()

# ================== 事件与命令 ==================
@bot.event
async def on_ready():
    load_settings()
    print(f'✅ Bot 已登录: {bot.user}')
    try:
        await bot.tree.sync()
        print("✅ 斜杠命令已同步")
    except Exception as e:
        print(f"❌ 命令同步失败: {e}")
    
    if not daily_push.is_running():
        daily_push.start()

@bot.tree.command(name="set_channel", description="设置推送频道（当前频道）")
async def set_channel(interaction: discord.Interaction):
    gid = interaction.guild_id
    if gid not in settings: settings[gid] = {}
    settings[gid]['channel_id'] = interaction.channel_id
    settings[gid]['min_importance'] = settings[gid].get('min_importance', 2)
    save_settings()
    await interaction.response.send_message(f"✅ 推送频道已设为 {interaction.channel.mention}", ephemeral=True)

@bot.tree.command(name="set_importance", description="设置最低重要程度")
@discord.app_commands.choices(level=[
    discord.app_commands.Choice(name="★ (全部)", value=1),
    discord.app_commands.Choice(name="★★ (中高)", value=2),
    discord.app_commands.Choice(name="★★★ (高)", value=3),
])
async def set_importance(interaction: discord.Interaction, level: discord.app_commands.Choice[int]):
    gid = interaction.guild_id
    if gid not in settings: settings[gid] = {}
    settings[gid]['min_importance'] = level.value
    save_settings()
    await interaction.response.send_message(f"✅ 最低重要程度设为 {level.name}", ephemeral=True)

@bot.tree.command(name="test_push", description="手动测试今日日历")
async def test_push(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    gid = interaction.guild_id
    # 默认设置
    min_imp = 2
    target_channel = interaction.channel
    
    # 读取配置
    if gid in settings:
        min_imp = settings[gid].get('min_importance', 2)
        saved_channel_id = settings[gid].get('channel_id')
        if saved_channel_id:
            c = interaction.guild.get_channel(saved_channel_id)
            if c: target_channel = c
            
    today = datetime.datetime.now(BJT).strftime("%Y-%m-%d")
    
    # 异步获取
    events = await fetch_us_events(today, min_imp)
    embeds = format_calendar(events, today, min_imp)

    if embeds:
        await target_channel.send(embed=embeds[0])
        for emb in embeds[1:]:
            await target_channel.send(embed=emb)
    
    # 如果尚未设置频道，提示设置
    if gid not in settings or 'channel_id' not in settings[gid]:
        view = SaveChannelView(gid, interaction.channel_id)
        await interaction.followup.send("测试已发送。检测到未设置默认频道，要将当前频道设为默认吗？", view=view, ephemeral=True)
    else:
        await interaction.followup.send(f"✅ 测试推送已发送至 {target_channel.mention}", ephemeral=True)

@bot.tree.command(name="disable_push", description="关闭本服务器推送")
async def disable_push(interaction: discord.Interaction):
    gid = interaction.guild_id
    if gid in settings:
        del settings[gid]
        save_settings()
        await interaction.response.send_message("🚫 已关闭本服务器推送", ephemeral=True)
    else:
        await interaction.response.send_message("本服务器未开启推送", ephemeral=True)

if __name__ == "__main__":
    bot.run(TOKEN)
