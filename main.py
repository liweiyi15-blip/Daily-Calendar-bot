import discord
from discord.ext import commands, tasks
import aiohttp
import datetime
import pytz
import json
import os
import re
import asyncio
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== Configuration ==================
TOKEN = os.getenv('TOKEN')
FMP_KEY = os.getenv('FMP_KEY')
SETTINGS_FILE = '/data/settings.json' 

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Timezones
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')
UTC = pytz.UTC

# API Endpoints
FMP_CAL_URL = "https://financialmodelingprep.com/stable/economic-calendar"
FMP_EARNINGS_URL = "https://financialmodelingprep.com/api/v3/earning_calendar"
FMP_QUOTE_URL = "https://financialmodelingprep.com/api/v3/quote/"

# Settings
MIN_MARKET_CAP = 10_000_000_000  # 财报过滤门槛：100亿美金市值 (防止垃圾股刷屏)
SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]
WEEKDAY_MAP = {
    'Monday': '周一', 'Tuesday': '周二', 'Wednesday': '周三', 'Thursday': '周四',
    'Friday': '周五', 'Saturday': '周六', 'Sunday': '周日'
}
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

settings = {}
translate_client = None

# ================== Google Translate 初始化 ==================
google_json_str = os.getenv('GOOGLE_JSON_CONTENT') 
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

# ================== 基础函数 ==================
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
    except Exception as e:
        print(f"保存设置失败: {e}")

def clean_title(title):
    return re.sub(r'\s*\([^)]*\)', '', str(title)).strip()

def translate_finance_text(text, target_lang='zh'):
    if not text or not translate_client:
        return str(text).strip()
    text = str(text).strip()
    if re.match(r'^-?\d+(\.\d+)?%?$', text): return text
    try:
        if translate_client.detect_language(text)['language'].startswith('zh'):
            return text
        result = translate_client.translate(text, source_language='en', target_language=target_lang)
        translated = result['translatedText']
        for abbr in ['CPI', 'PPI', 'GDP', 'ISM', 'PMI', 'FOMC', 'Fed', 'JOLTS', 'CFTC', 'S&P', 'QoQ', 'MoM', 'YoY']:
            translated = re.sub(rf'\b{abbr}\b', abbr, translated, flags=re.IGNORECASE)
        return translated.strip()
    except:
        return text

# ================== 核心逻辑：经济日历 ==================
async def fetch_us_events(target_date_str, min_importance=2):
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except ValueError: return []

    params = {"from": target_date_str, "to": target_date_str, "apikey": FMP_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FMP_CAL_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data_json = await resp.json()

        events = {}
        start_bjt = BJT.localize(datetime.datetime.combine(target_date, datetime.time(8, 0)))
        end_bjt = start_bjt + datetime.timedelta(days=1)

        for item in data_json:
            if item.get("country") != "US": continue
            imp_num = IMPACT_MAP.get(item.get("impact", "Low").capitalize(), 1)
            if imp_num < min_importance: continue

            dt_str = item.get("date")
            if not dt_str: continue
            utc_dt = UTC.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"))
            bjt_dt = utc_dt.astimezone(BJT)
            if not (start_bjt <= bjt_dt < end_bjt): continue

            et_dt = utc_dt.astimezone(ET)
            time_display = f"{bjt_dt.strftime('%H:%M')} ({et_dt.strftime('%H:%M')} ET)"
            raw_title = item.get("event", "")
            title = clean_title(raw_title)
            
            # 异步调用中尽量避免同步的翻译，但这里量不大暂且保留
            translated_title = translate_finance_text(title)
            forecast = translate_finance_text(item.get("estimate", "") or "—")
            previous = translate_finance_text(item.get("previous", "") or "—")

            event = {
                "time": time_display, "importance": "★" * imp_num, "title": translated_title,
                "forecast": forecast, "previous": previous, "orig_title": raw_title,
                "bjt_timestamp": bjt_dt, "date": dt_str
            }
            key = title.lower()
            if key not in events or dt_str > events[key].get("date", ""):
                events[key] = event
        return sorted(events.values(), key=lambda x: x["bjt_timestamp"])
    except Exception as e:
        print(f"Events API Error: {e}")
        return []

# ================== 核心逻辑：财报获取 ==================
async def fetch_earnings(date_str):
    """
    获取指定日期的财报，并按市值过滤
    """
    params = {"from": date_str, "to": date_str, "apikey": FMP_KEY}
    async with aiohttp.ClientSession() as session:
        try:
            # 1. 获取财报名单
            async with session.get(FMP_EARNINGS_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                calendar_data = await resp.json()
            
            if not calendar_data: return {}

            # 2. 提取 Symbol，去重
            symbols = list(set([item['symbol'] for item in calendar_data if item.get('symbol')]))
            if not symbols: return {}

            # 3. 分批查询市值 (FMP Batch Quote 限制)
            important_stocks = []
            chunk_size = 50 # 每次查询50个，避免URL过长
            
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                quote_url = f"{FMP_QUOTE_URL}{chunk_str}?apikey={FMP_KEY}"
                
                try:
                    async with session.get(quote_url, timeout=10) as q_resp:
                        if q_resp.status == 200:
                            quotes = await q_resp.json()
                            for q in quotes:
                                mcap = q.get('marketCap', 0)
                                if mcap and mcap >= MIN_MARKET_CAP:
                                    important_stocks.append({
                                        'symbol': q['symbol'],
                                        'name': q.get('name', q['symbol']),
                                        'marketCap': mcap,
                                        # 从原始 calendar_data 找回发布时间 (bmo/amc)
                                        'time': next((x['time'] for x in calendar_data if x['symbol'] == q['symbol']), 'bmo')
                                    })
                except Exception as e:
                    print(f"Quote fetch error: {e}")
                    continue
                
                await asyncio.sleep(0.1) # 避免触发速率限制

            # 4. 分组排序
            result = {'bmo': [], 'amc': [], 'other': []}
            # 按市值倒序排列
            important_stocks.sort(key=lambda x: x['marketCap'], reverse=True)

            for stock in important_stocks:
                time_code = stock['time'].lower()
                entry = f"**{stock['symbol']}** ({stock['name']})"
                if time_code == 'bmo':
                    result['bmo'].append(entry)
                elif time_code == 'amc':
                    result['amc'].append(entry)
                else:
                    result['other'].append(entry)
            
            return result

        except Exception as e:
            print(f"Fetch Earnings Error: {e}")
            return {}

# ================== 格式化函数 ==================
def format_calendar_embed(events, date_str, min_imp):
    weekday_cn = WEEKDAY_MAP.get(datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime('%A'), '')
    title = f"📅 今日宏观事件 ({date_str} {weekday_cn})"
    
    if not events:
        embed = discord.Embed(title=title, description=f"今日无 ★{'★'*(min_imp-1)} 以上事件", color=0x3498db)
        return [embed]

    embed = discord.Embed(title=title, color=0x3498db)
    for e in events:
        field_name = f"{e['time']} {e['title']}"
        if any(k in e['orig_title'] for k in SPEECH_KEYWORDS):
            val = f"影响: {e['importance']}"
        else:
            val = f"影响: {e['importance']} | 预期: {e['forecast']} | 前值: {e['previous']}"
        embed.add_field(name=field_name, value=val, inline=False)
    return [embed]

def format_earnings_embed(earnings_data, date_str):
    weekday_cn = WEEKDAY_MAP.get(datetime.datetime.strptime(date_str, "%Y-%m-%d").strftime('%A'), '')
    title = f"💰 明日重点财报 ({date_str} {weekday_cn})"
    
    # 检查是否为空
    if not any(earnings_data.values()):
        return None # 没重要财报就不发了，或者返回一个空提示
    
    embed = discord.Embed(title=title, description=f"筛选市值 > {MIN_MARKET_CAP//100000000} 亿美元", color=0xf1c40f)
    
    if earnings_data['bmo']:
        content = "\n".join(earnings_data['bmo'][:15]) # 最多显示15个，防止超长
        if len(earnings_data['bmo']) > 15: content += f"\n...以及其他 {len(earnings_data['bmo'])-15} 家"
        embed.add_field(name="☀️ 盘前 (Before Open)", value=content, inline=False)
        
    if earnings_data['amc']:
        content = "\n".join(earnings_data['amc'][:15])
        if len(earnings_data['amc']) > 15: content += f"\n...以及其他 {len(earnings_data['amc'])-15} 家"
        embed.add_field(name="🌙 盘后 (After Close)", value=content, inline=False)

    if not earnings_data['bmo'] and not earnings_data['amc']:
        embed.description = "明日无重点大盘股财报"
        
    return embed

# ================== 按钮视图 ==================
class SaveChannelView(discord.ui.View):
    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.channel_id = channel_id

    @discord.ui.button(label="设为默认频道", style=discord.ButtonStyle.primary)
    async def save(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.guild_id not in settings: settings[self.guild_id] = {}
        settings[self.guild_id]['channel_id'] = self.channel_id
        save_settings()
        await interaction.response.send_message("✅ 已成功设为默认推送频道！", ephemeral=True)
        self.stop()

# ================== 统一主循环 ==================
@tasks.loop(minutes=1)
async def main_loop():
    now_bjt = datetime.datetime.now(BJT)
    current_time = now_bjt.strftime('%H:%M')
    
    # print(f"💓 Heartbeat: {current_time}") # 调试用，可注释

    # ----------------- 任务1: 08:00 发送今日宏观事件 -----------------
    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        lock_file = f"/data/push_event_{today_str}.lock"
        
        if not os.path.exists(lock_file):
            with open(lock_file, "w") as f: f.write("locked")
            print(f"🚀 [任务1] 开始推送宏观事件: {today_str}")
            
            for gid, conf in settings.items():
                channel = bot.get_channel(conf.get('channel_id'))
                if not channel: continue
                
                try:
                    events = await fetch_us_events(today_str, conf.get('min_importance', 2))
                    embeds = format_calendar_embed(events, today_str, conf.get('min_importance', 2))
                    for emb in embeds: await channel.send(embed=emb)
                except Exception as e:
                    print(f"推送事件错误 {gid}: {e}")

    # ----------------- 任务2: 20:00 发送明日财报 -----------------
    elif now_bjt.hour == 20 and 0 <= now_bjt.minute < 5:
        # 计算明天日期
        tomorrow = now_bjt + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        lock_file = f"/data/push_earnings_{tomorrow_str}.lock"
        
        if not os.path.exists(lock_file):
            with open(lock_file, "w") as f: f.write("locked")
            print(f"🚀 [任务2] 开始推送明日财报: {tomorrow_str}")
            
            # 为了节省API额度，统一获取一次数据，然后分发
            earnings_data = await fetch_earnings(tomorrow_str)
            embed = format_earnings_embed(earnings_data, tomorrow_str)
            
            if embed: # 只有当有内容时才发送
                for gid, conf in settings.items():
                    channel = bot.get_channel(conf.get('channel_id'))
                    if not channel: continue
                    try:
                        await channel.send(embed=embed)
                    except Exception as e:
                        print(f"推送财报错误 {gid}: {e}")
            else:
                print("明日无重要财报，跳过推送")

@main_loop.before_loop
async def before_loop():
    await bot.wait_until_ready()

# ================== Commands & Events ==================
@bot.event
async def on_ready():
    load_settings()
    print(f'✅ Bot 已登录: {bot.user}')
    try:
        await bot.tree.sync()
        print("✅ 斜杠命令已同步")
    except Exception as e: print(f"同步失败: {e}")
    if not main_loop.is_running(): main_loop.start()

@bot.tree.command(name="set_channel", description="设置推送频道")
async def set_channel(interaction: discord.Interaction):
    gid = interaction.guild_id
    if gid not in settings: settings[gid] = {}
    settings[gid]['channel_id'] = interaction.channel_id
    save_settings()
    await interaction.response.send_message(f"✅ 频道已绑定到 {interaction.channel.mention}", ephemeral=True)

@bot.tree.command(name="set_importance", description="设置宏观事件最低星级")
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
    await interaction.response.send_message(f"✅ 最低星级设为 {level.name}", ephemeral=True)

@bot.tree.command(name="test_earnings", description="手动测试：查看明天的财报")
async def test_earnings(interaction: discord.Interaction):
    await interaction.response.defer()
    tomorrow = datetime.datetime.now(BJT) + datetime.timedelta(days=1)
    date_str = tomorrow.strftime("%Y-%m-%d")
    
    data = await fetch_earnings(date_str)
    embed = format_earnings_embed(data, date_str)
    
    if embed:
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send(f"📅 {date_str} 暂无重点财报 (市值 > 100亿)", ephemeral=True)

# ================== Start ==================
if __name__ == "__main__":
    bot.run(TOKEN)
