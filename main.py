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
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession # 核心：伪装浏览器绕过 Yahoo 反爬
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== 1. 系统配置 ==================
# 强制日志实时输出，防止 Railway 卡顿
sys.stdout.reconfigure(line_buffering=True)

TOKEN = os.getenv('TOKEN')
FMP_KEY = os.getenv('FMP_KEY') # 仅用于宏观日历
SETTINGS_FILE = '/data/settings.json' 

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# 时区设置
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')
UTC = pytz.UTC

# ================== 2. 数据源 URL ==================
FMP_CAL_URL = "https://financialmodelingprep.com/stable/economic-calendar"
YAHOO_CAL_URL = "https://finance.yahoo.com/calendar/earnings"
GITHUB_SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"

# ================== 3. 核心关注名单 (带🔥) ==================
HOT_STOCKS = {
    # === 用户特别关注 ===
    "RKLB", "COIN", "NVDA", "TSLA", "HOOD", "PLTR",
    # === 芯片/半导体 ===
    "AMD", "INTC", "TSM", "ASML", "ARM", "AVGO", "QCOM", "MU", "SMCI", "MRVL",
    # === 科技巨头 ===
    "AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "NFLX", "CRM", "ADBE", "ORCL",
    # === 热门成长/SaaS ===
    "U", "DKNG", "ROKU", "SHOP", "SQ", "ZM", "CRWD", "NET", "SNOW", "DDOG", "TEAM", "ZS", "PANW",
    # === 加密货币 ===
    "MSTR", "MARA", "RIOT", "CLSK",
    # === 太空/新能源/硬科技 ===
    "ASTS", "SPCE", "IONQ", "RIVN", "LCID", "NIO", "XPEV", "LI", "ENPH", "CVNA",
    # === 金融科技 ===
    "SOFI", "UPST", "AFRM", "PYPL",
    # === WSB/网红 ===
    "GME", "AMC", "RDDT", "DJT",
    # === 热门中概 ===
    "BABA", "PDD", "JD", "BIDU", "BILI", "FUTU"
}

FALLBACK_GIANTS = {"NVDA", "AAPL", "MSFT", "AMZN", "TSLA", "GOOG", "META"}

SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]
WEEKDAY_MAP = {
    'Monday': '周一', 'Tuesday': '周二', 'Wednesday': '周三', 'Thursday': '周四',
    'Friday': '周五', 'Saturday': '周六', 'Sunday': '周日'
}
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

# 全局变量
settings = {}
sp500_symbols = set() 
translate_client = None

# ================== 4. 辅助工具函数 ==================
def log(msg):
    print(msg, flush=True)

def safe_print_error(prefix, error_obj):
    err_str = str(error_obj)
    if FMP_KEY:
        err_str = err_str.replace(FMP_KEY, "******")
    log(f"❌ {prefix}: {err_str}")

# 初始化 Google 翻译
google_json_str = os.getenv('GOOGLE_JSON_CONTENT') 
google_key_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
try:
    if google_json_str:
        cred_info = json.loads(google_json_str)
        credentials = service_account.Credentials.from_service_account_info(cred_info)
        translate_client = translate.Client(credentials=credentials)
        log('✅ Google Translate SDK (Env) 初始化成功')
    elif google_key_path and os.path.exists(google_key_path):
        credentials = service_account.Credentials.from_service_account_file(google_key_path)
        translate_client = translate.Client(credentials=credentials)
        log('✅ Google Translate SDK (File) 初始化成功')
except Exception as e:
    safe_print_error("SDK 初始化失败", e)

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
    if not text or not translate_client: return str(text).strip()
    text = str(text).strip()
    if re.match(r'^-?\d+(\.\d+)?%?$', text): return text
    try:
        if translate_client.detect_language(text)['language'].startswith('zh'): return text
        result = translate_client.translate(text, source_language='en', target_language=target_lang)
        t = result['translatedText']
        for abbr in ['CPI', 'PPI', 'GDP', 'FOMC', 'Fed', 'YoY', 'MoM']:
            t = re.sub(rf'\b{abbr}\b', abbr, t, flags=re.IGNORECASE)
        return t.strip()
    except: return text

# ================== 5. 核心逻辑：更新白名单 ==================
async def update_sp500_list():
    global sp500_symbols
    log("🔄 正在从 GitHub 更新 S&P 500 名单...")
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(GITHUB_SP500_URL, timeout=15) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    new_list = set()
                    for line in text.split('\n')[1:]:
                        parts = line.split(',')
                        if parts and parts[0]:
                            new_list.add(parts[0].strip().replace('.', '-'))
                    
                    if len(new_list) > 400:
                        sp500_symbols = new_list
                        log(f"✅ S&P 500 更新成功: {len(sp500_symbols)} 只")
                    else:
                        log("⚠️ GitHub 数据异常，使用备用名单")
                        sp500_symbols.update(FALLBACK_GIANTS)
                else:
                    log(f"⚠️ GitHub 访问失败: {resp.status}")
                    sp500_symbols.update(FALLBACK_GIANTS)
        except Exception as e:
            safe_print_error("更新名单失败", e)
            sp500_symbols.update(FALLBACK_GIANTS)

# ================== 6. 核心逻辑：宏观日历 (FMP) ==================
async def fetch_us_events(target_date_str, min_importance=2):
    try: target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except: return []
    
    params = {"from": target_date_str, "to": target_date_str, "apikey": FMP_KEY}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(FMP_CAL_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
        
        events = []
        start = BJT.localize(datetime.datetime.combine(target_date, datetime.time(8, 0)))
        end = start + datetime.timedelta(days=1)
        
        for item in data:
            if item.get("country") != "US": continue
            imp = IMPACT_MAP.get(item.get("impact", "Low").capitalize(), 1)
            if imp < min_importance: continue
            
            dt_str = item.get("date")
            if not dt_str: continue
            utc = UTC.localize(datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"))
            bjt = utc.astimezone(BJT)
            if not (start <= bjt < end): continue

            et = utc.astimezone(ET)
            time_str = f"{bjt.strftime('%H:%M')} ({et.strftime('%H:%M')} ET)"
            title = clean_title(item.get("event", ""))
            
            events.append({
                "time": time_str,
                "importance": "★" * imp,
                "title": translate_finance_text(title),
                "forecast": translate_finance_text(item.get("estimate", "") or "—"),
                "previous": translate_finance_text(item.get("previous", "") or "—"),
                "orig_title": title,
                "bjt_timestamp": bjt
            })
        
        unique_events = {}
        for e in events:
            key = e['title']
            if key not in unique_events or e['bjt_timestamp'] < unique_events[key]['bjt_timestamp']:
                unique_events[key] = e
        return sorted(unique_events.values(), key=lambda x: x["bjt_timestamp"])
    except Exception as e:
        safe_print_error("Events API Error", e)
        return []

# ================== 7. 核心逻辑：财报获取 (Yahoo 爬虫版) ==================
async def fetch_earnings(date_str):
    if not sp500_symbols: await update_sp500_list()
    
    log(f"🕷️ [爬虫] 伪装 Chrome 抓取 Yahoo: {date_str}")
    
    important_stocks = []
    
    try:
        async with AsyncSession(impersonate="chrome110") as session:
            for offset in [0, 100]:
                url = f"{YAHOO_CAL_URL}?day={date_str}&offset={offset}&size=100"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": "https://finance.yahoo.com/",
                    "Accept-Language": "en-US,en;q=0.9"
                }

                resp = await session.get(url, headers=headers, timeout=15)
                
                if resp.status_code != 200:
                    log(f"❌ Yahoo 返回状态码: {resp.status_code}")
                    break
                
                soup = BeautifulSoup(resp.text, 'html.parser')
                rows = soup.find_all('tr')
                
                if not rows:
                    log(f"⚠️ 页面解析为空 (Offset {offset})")
                    continue

                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) < 3: continue
                    
                    sym_tag = cols[0].find('a')
                    if not sym_tag: continue
                    symbol = sym_tag.text.strip()
                    time_text = cols[2].text.strip()
                    
                    is_hot = symbol in HOT_STOCKS
                    is_sp500 = symbol in sp500_symbols
                    
                    if is_hot or is_sp500:
                        time_code = 'other'
                        # 优化时间判断：不区分大小写
                        t_lower = time_text.lower()
                        if "before" in t_lower or "open" in t_lower: time_code = 'bmo'
                        elif "after" in t_lower or "close" in t_lower: time_code = 'amc'
                        
                        important_stocks.append({
                            'symbol': symbol,
                            'time': time_code,
                            'is_hot': is_hot
                        })
                
                await asyncio.sleep(1)

        log(f"✅ 抓取完成，筛选后剩余 {len(important_stocks)} 家")
        
        unique_dict = {s['symbol']: s for s in important_stocks}
        final_list = list(unique_dict.values())
        final_list.sort(key=lambda x: x['is_hot'], reverse=True)
        
        return final_list

    except Exception as e:
        safe_print_error("Yahoo 爬虫严重错误", e)
        return []

# ================== 8. 格式化输出 (极简两列版) ==================
def format_calendar_embed(events, date_str, min_imp):
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        month_day = dt.strftime("%m月%d日")
        weekday_cn = WEEKDAY_MAP.get(dt.strftime('%A'), '')
        title = f"今日热点（{month_day} {weekday_cn}）"
    except:
        title = f"今日热点 ({date_str})"

    if not events: return [discord.Embed(title=title, description="无重要事件", color=0x00FF00)]
    
    embed = discord.Embed(title=title, color=0x00FF00)
    for e in events:
        val = f"影响: {e['importance']}" if any(k in e['orig_title'] for k in SPEECH_KEYWORDS) else \
              f"影响: {e['importance']} | 预期: {e['forecast']} | 前值: {e['previous']}"
        embed.add_field(name=f"{e['time']} {e['title']}", value=val, inline=False)
    return [embed]

def format_earnings_embed(stocks, date_str):
    if not stocks: return None
    
    # 1. 优化日期显示
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        weekday_cn = WEEKDAY_MAP.get(dt.strftime('%A'), '')
        title = f"💰 重点财报 | {date_str} ({weekday_cn})"
    except:
        title = f"💰 重点财报 ({date_str})"

    embed = discord.Embed(title=title, color=0xf1c40f)
    
    # 2. 构建紧凑列表 (带超链接)
    def build_compact_list(items):
        line_list = []
        for s in items:
            icon = "🔥" if s['is_hot'] else ""
            # 给代码加上 Yahoo 链接
            symbol_text = f"[`{s['symbol']}`](https://finance.yahoo.com/quote/{s['symbol']})"
            line_list.append(f"{icon}{symbol_text}")
        return " , ".join(line_list)

    bmo = [s for s in stocks if s['time'] == 'bmo']
    amc = [s for s in stocks if s['time'] == 'amc']
    other = [s for s in stocks if s['time'] == 'other']

    # 3. 左右两列布局 (inline=True)
    # 盘前 (左)
    if bmo: 
        val = build_compact_list(bmo)
        if len(val) > 1024: val = val[:1020] + "..."
        embed.add_field(name="☀️ 盘前", value=val, inline=True)
    
    # 盘后 (右)
    if amc: 
        val = build_compact_list(amc)
        if len(val) > 1024: val = val[:1020] + "..."
        embed.add_field(name="🌙 盘后", value=val, inline=True)
    
    # 其他/未定 (横跨下方)
    if other:
        val = build_compact_list(other)
        if len(val) > 1024: val = val[:1020] + "..."
        embed.add_field(name="🕒 时间未定", value=val, inline=False)

    embed.set_footer(text="数据来源: Yahoo Finance")
    return embed

# ================== 9. 定时任务与事件 ==================
@tasks.loop(minutes=1)
async def main_loop():
    now = datetime.datetime.now(BJT)
    # 08:00 宏观
    if now.hour == 8 and 0 <= now.minute < 5:
        today = now.strftime("%Y-%m-%d")
        lock = f"/data/evt_{today}.lock"
        if not os.path.exists(lock):
            with open(lock, "w") as f: f.write("x")
            log(f"🚀 推送宏观: {today}")
            for gid, conf in settings.items():
                ch = bot.get_channel(conf.get('channel_id'))
                if ch:
                    evts = await fetch_us_events(today, conf.get('min_importance', 2))
                    for em in format_calendar_embed(evts, today, conf.get('min_importance', 2)): await ch.send(embed=em)

    # 20:00 财报
    elif now.hour == 20 and 0 <= now.minute < 5:
        tmr = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        lock = f"/data/ern_{tmr}.lock"
        if not os.path.exists(lock):
            with open(lock, "w") as f: f.write("x")
            await update_sp500_list()
            log(f"🚀 推送财报: {tmr}")
            data = await fetch_earnings(tmr)
            embed = format_earnings_embed(data, tmr)
            if embed:
                for gid, conf in settings.items():
                    ch = bot.get_channel(conf.get('channel_id'))
                    if ch: await ch.send(embed=embed)

@main_loop.before_loop
async def before_loop():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    load_settings()
    log(f'✅ Bot 已登录: {bot.user}')
    await bot.tree.sync()
    await update_sp500_list()
    if not main_loop.is_running(): main_loop.start()

# ================== 10. 命令 ==================
@bot.tree.command(name="set_channel", description="设置推送频道")
async def set_channel(interaction: discord.Interaction):
    gid = interaction.guild_id
    if gid not in settings: settings[gid] = {}
    settings[gid]['channel_id'] = interaction.channel_id
    save_settings()
    await interaction.response.send_message(f"✅ 绑定成功", ephemeral=True)

@bot.tree.command(name="test_earnings", description="测试财报")
async def test_earnings(interaction: discord.Interaction, date: str = None):
    await interaction.response.defer()
    if not date: date = (datetime.datetime.now(BJT) + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    stocks = await fetch_earnings(date)
    embed = format_earnings_embed(stocks, date)
    if embed: await interaction.followup.send(embed=embed)
    else: await interaction.followup.send(f"📅 **{date}** 无重点财报", ephemeral=True)

@bot.tree.command(name="test_push", description="测试宏观日历")
async def test_push(interaction: discord.Interaction):
    await interaction.response.defer()
    today = datetime.datetime.now(BJT).strftime("%Y-%m-%d")
    evts = await fetch_us_events(today, 2)
    for em in format_calendar_embed(evts, today, 2): await interaction.followup.send(embed=em)

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
