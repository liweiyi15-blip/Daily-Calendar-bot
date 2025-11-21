import discord
from discord.ext import commands, tasks
import aiohttp
import datetime
import pytz
import json
import os
import re
import asyncio
import sys
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== 核心设置：强制日志实时输出 ==================
# 解决 Railway 日志卡顿、不显示的问题
sys.stdout.reconfigure(line_buffering=True)

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
FMP_EARNINGS_URL = "https://financialmodelingprep.com/stable/earnings-calendar"
FMP_QUOTE_URL = "https://financialmodelingprep.com/api/v3/quote/"

# Settings
# 过滤门槛：100亿美金。
# 【注意】代码逻辑已修改：如果API查不到市值(返回0)，也会显示，防止误杀大公司
MIN_MARKET_CAP = 10_000_000_000 
SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]
WEEKDAY_MAP = {
    'Monday': '周一', 'Tuesday': '周二', 'Wednesday': '周三', 'Thursday': '周四',
    'Friday': '周五', 'Saturday': '周六', 'Sunday': '周日'
}
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

settings = {}
translate_client = None

# ================== 辅助函数：带 Flush 的打印 ==================
def log(msg):
    """强制刷新日志，防止在 Railway 上卡住"""
    print(msg, flush=True)

def safe_print_error(prefix, error_obj):
    """日志脱敏：隐藏 API Key"""
    err_str = str(error_obj)
    if FMP_KEY:
        err_str = err_str.replace(FMP_KEY, "******")
    log(f"❌ {prefix}: {err_str}")

# ================== Google Translate 初始化 ==================
google_json_str = os.getenv('GOOGLE_JSON_CONTENT') 
google_key_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

try:
    if google_json_str:
        cred_info = json.loads(google_json_str)
        credentials = service_account.Credentials.from_service_account_info(cred_info)
        translate_client = translate.Client(credentials=credentials)
        log('✅ Google Translate SDK (Env String) 初始化成功')
    elif google_key_path and os.path.exists(google_key_path):
        credentials = service_account.Credentials.from_service_account_file(google_key_path)
        translate_client = translate.Client(credentials=credentials)
        log('✅ Google Translate SDK (File Path) 初始化成功')
    else:
        log('⚠️ 未检测到 Google 凭证，翻译功能将不可用')
except Exception as e:
    safe_print_error("SDK 初始化失败", e)

# ================== 基础函数 ==================
def load_settings():
    global settings
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                settings = {int(k): v for k, v in raw.items()}
            log(f"已加载设置: {len(settings)} 个服务器")
        except Exception as e:
            log(f"加载设置失败: {e}")
            settings = {}
    else:
        settings = {}

def save_settings():
    try:
        os.makedirs('/data', exist_ok=True)
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception as e:
        log(f"保存设置失败: {e}")

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
        safe_print_error("Events API Error", e)
        return []

# ================== 核心逻辑：财报获取 (防误杀版) ==================
async def fetch_earnings(date_str):
    log(f"🔍 [调试] 开始查询 {date_str} 的财报...")
    params = {"from": date_str, "to": date_str, "apikey": FMP_KEY}
    
    async with aiohttp.ClientSession() as session:
        try:
            # 1. 获取财报名单
            async with session.get(FMP_EARNINGS_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                calendar_data = await resp.json()
            
            if not calendar_data:
                log(f"⚠️ [调试] FMP 返回了空列表，日期: {date_str}")
                return {}

            # 2. 提取 Symbol
            symbols = list(set([item['symbol'] for item in calendar_data if item.get('symbol')]))
            log(f"✅ [调试] 名单共找到 {len(symbols)} 家公司 (包含 NVDA: {'NVDA' in symbols})")

            # 3. 分批查询市值
            important_stocks = []
            chunk_size = 50 
            
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                quote_url = f"{FMP_QUOTE_URL}{chunk_str}?apikey={FMP_KEY}"
                
                try:
                    async with session.get(quote_url, timeout=10) as q_resp:
                        if q_resp.status != 200:
                            log(f"❌ [调试] Quote API 状态码: {q_resp.status}")
                        
                        quotes = await q_resp.json()
                        quote_map = {q['symbol']: q.get('marketCap', 0) for q in quotes}

                        for symbol in chunk:
                            mcap = quote_map.get(symbol, 0) 
                            
                            # 匹配原始数据里的发布时间
                            orig_item = next((x for x in calendar_data if x['symbol'] == symbol), None)
                            stock_name = next((q['name'] for q in quotes if q['symbol'] == symbol), symbol)

                            # 【核心逻辑修改】
                            # 1. 如果市值 >= 门槛，保留
                            # 2. 如果市值 == 0 (说明API没查到，或者是新股)，也保留！标记为未知，防止误杀
                            if mcap >= MIN_MARKET_CAP or mcap == 0:
                                important_stocks.append({
                                    'symbol': symbol,
                                    'name': stock_name,
                                    'marketCap': mcap,
                                    'time': orig_item['time'] if orig_item else 'bmo'
                                })

                except Exception as e:
                    safe_print_error(f"Batch {i} Error", e)
                    continue
                
                await asyncio.sleep(0.1)

            log(f"✅ [调试] 最终列表有 {len(important_stocks)} 家")

            # 4. 分组排序
            result = {'bmo': [], 'amc': [], 'other': []}
            # 按市值倒序 (市值0的会排在最后)
            important_stocks.sort(key=lambda x: x['marketCap'], reverse=True)

            for stock in important_stocks:
                time_code = stock['time'].lower()
                # 显示格式优化
                mcap_str = f"{stock['marketCap']/100000000:.1f}亿" if stock['marketCap'] > 0 else "市值未知"
                entry = f"**{stock['symbol']}** ({mcap_str})"
                
                if time_code == 'bmo':
                    result['bmo'].append(entry)
                elif time_code == 'amc':
                    result['amc'].append(entry)
                else:
                    result['other'].append(entry)
            
            return result

        except Exception as e:
            safe_print_error("Fetch Earnings Error", e)
            return {}

# ================== 格式化函数 (防爆版) ==================
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
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        weekday_cn = WEEKDAY_MAP.get(dt.strftime('%A'), '')
    except:
        weekday_cn = ""
        
    title = f"💰 重点财报日历 ({date_str} {weekday_cn})"
    
    if not earnings_data or not any(earnings_data.values()):
        return None 
    
    embed = discord.Embed(title=title, description=f"筛选市值 > {MIN_MARKET_CAP//100000000} 亿美元 (含未知市值)", color=0xf1c40f)
    
    # 辅助函数：强制截断，防止 Discord 消息超长报错
    def safe_content(items):
        content = ""
        for item in items:
            # 预判长度：如果加上这一行会超过 900 字符 (预留缓冲)，就停止
            if len(content) + len(item) + 50 > 900: 
                content += f"\n...以及其他 {len(items) - items.index(item)} 家"
                break
            content += item + "\n"
        return content if content else "无"

    if earnings_data.get('bmo'):
        embed.add_field(name="☀️ 盘前 (Before Open)", value=safe_content(earnings_data['bmo']), inline=False)
        
    if earnings_data.get('amc'):
        embed.add_field(name="🌙 盘后 (After Close)", value=safe_content(earnings_data['amc']), inline=False)

    if earnings_data.get('other'):
        embed.add_field(name="🕒 时间未定", value=safe_content(earnings_data['other']), inline=False)

    return embed

# ================== 统一主循环 ==================
@tasks.loop(minutes=1)
async def main_loop():
    now_bjt = datetime.datetime.now(BJT)
    
    # ----------------- 任务1: 08:00 发送今日宏观事件 -----------------
    if now_bjt.hour == 8 and 0 <= now_bjt.minute < 5:
        today_str = now_bjt.strftime("%Y-%m-%d")
        os.makedirs('/data', exist_ok=True)
        lock_file = f"/data/push_event_{today_str}.lock"
        
        if not os.path.exists(lock_file):
            with open(lock_file, "w") as f: f.write("locked")
            log(f"🚀 [任务1] 开始推送宏观事件: {today_str}")
            
            for gid, conf in settings.items():
                channel = bot.get_channel(conf.get('channel_id'))
                if not channel: continue
                try:
                    events = await fetch_us_events(today_str, conf.get('min_importance', 2))
                    embeds = format_calendar_embed(events, today_str, conf.get('min_importance', 2))
                    for emb in embeds: await channel.send(embed=emb)
                except Exception as e:
                    safe_print_error(f"推送事件错误 {gid}", e)

    # ----------------- 任务2: 20:00 发送明日财报 -----------------
    elif now_bjt.hour == 20 and 0 <= now_bjt.minute < 5:
        tomorrow = now_bjt + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        os.makedirs('/data', exist_ok=True)
        lock_file = f"/data/push_earnings_{tomorrow_str}.lock"
        
        if not os.path.exists(lock_file):
            with open(lock_file, "w") as f: f.write("locked")
            log(f"🚀 [任务2] 开始推送明日财报: {tomorrow_str}")
            
            earnings_data = await fetch_earnings(tomorrow_str)
            embed = format_earnings_embed(earnings_data, tomorrow_str)
            
            if embed:
                for gid, conf in settings.items():
                    channel = bot.get_channel(conf.get('channel_id'))
                    if not channel: continue
                    try:
                        await channel.send(embed=embed)
                    except Exception as e:
                        safe_print_error(f"推送财报错误 {gid}", e)
            else:
                log("明日无重要财报，跳过推送")

@main_loop.before_loop
async def before_loop():
    await bot.wait_until_ready()

# ================== Commands & Events ==================
@bot.event
async def on_ready():
    load_settings()
    log(f'✅ Bot 已登录: {bot.user}')
    try:
        await bot.tree.sync()
        log("✅ 斜杠命令已同步")
    except Exception as e: log(f"同步失败: {e}")
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

@bot.tree.command(name="test_push", description="手动测试今日宏观事件")
async def test_push(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    today = datetime.datetime.now(BJT).strftime("%Y-%m-%d")
    gid = interaction.guild_id
    min_imp = settings.get(gid, {}).get('min_importance', 2)
    
    events = await fetch_us_events(today, min_imp)
    embeds = format_calendar_embed(events, today, min_imp)
    
    if embeds:
        await interaction.followup.send(embed=embeds[0])
        for emb in embeds[1:]: await interaction.followup.send(embed=emb)
    else:
        await interaction.followup.send("今日无相关事件", ephemeral=True)

@bot.tree.command(name="test_earnings", description="测试财报：默认明天，也可指定日期 (格式: 2025-11-21)")
async def test_earnings(interaction: discord.Interaction, date: str = None):
    await interaction.response.defer()
    
    log(f"👉 收到命令 /test_earnings date={date}")
    
    if date:
        target_date_str = date
    else:
        tomorrow = datetime.datetime.now(BJT) + datetime.timedelta(days=1)
        target_date_str = tomorrow.strftime("%Y-%m-%d")
    
    try:
        data = await fetch_earnings(target_date_str)
        
        if not data:
             log("⚠️ 数据为空")
             await interaction.followup.send(f"📅 **{target_date_str}** 数据为空或获取失败，请检查后台日志。", ephemeral=True)
             return

        embed = format_earnings_embed(data, target_date_str)
        
        if embed:
            log("✅ Embed 生成成功，正在发送...")
            await interaction.followup.send(embed=embed)
        else:
            log("⚠️ Embed 生成为空 (可能被市值过滤)")
            await interaction.followup.send(f"📅 **{target_date_str}** 暂无重点财报", ephemeral=True)
            
    except Exception as e:
        safe_print_error("命令执行出错", e)
        await interaction.followup.send(f"❌ 出错，请查看后台日志", ephemeral=True)

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
