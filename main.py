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
from bs4 import BeautifulSoup # 必须安装 beautifulsoup4
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account

# ================== 日志配置 ==================
sys.stdout.reconfigure(line_buffering=True)

# ================== Configuration ==================
TOKEN = os.getenv('TOKEN')
FMP_KEY = os.getenv('FMP_KEY') # 虽然查财报不用了，但宏观日历可能还得用
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
GITHUB_SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
# [修改] 数据源换成 Yahoo Finance
YAHOO_CAL_URL = "https://finance.yahoo.com/calendar/earnings"

# ================== 🌟 关注名单 ==================
HOT_STOCKS = {
    "RKLB", "COIN", "NVDA", "AMD", "INTC", "TSM", "ASML", "ARM", "AVGO", "QCOM", "MU", "SMCI",
    "AAPL", "MSFT", "AMZN", "GOOG", "GOOGL", "META", "TSLA", "NFLX", "CRM", "ADBE", "ORCL",
    "PLTR", "U", "DKNG", "ROKU", "SHOP", "SQ", "ZM", "CRWD", "NET", "SNOW", "DDOG", "TEAM", "ZS", "PANW",
    "MSTR", "MARA", "RIOT", "CLSK", "HOOD",
    "ASTS", "SPCE", "IONQ", "RIVN", "LCID", "NIO", "XPEV", "LI", "ENPH", "CVNA",
    "SOFI", "UPST", "AFRM", "PYPL",
    "GME", "AMC", "RDDT", "DJT",
    "BABA", "PDD", "JD", "BIDU", "BILI", "FUTU"
}

# S&P 500 备用 (防止 GitHub 挂了)
FALLBACK_GIANTS = {"NVDA", "AAPL", "MSFT", "AMZN", "TSLA", "GOOG", "META", "AMD"}

# Settings
SPEECH_KEYWORDS = ["Speech", "Testimony", "Remarks", "Press Conference", "Hearing"]
WEEKDAY_MAP = {
    'Monday': '周一', 'Tuesday': '周二', 'Wednesday': '周三', 'Thursday': '周四',
    'Friday': '周五', 'Saturday': '周六', 'Sunday': '周日'
}
IMPACT_MAP = {"Low": 1, "Medium": 2, "High": 3}

settings = {}
sp500_symbols = set() 
translate_client = None

# ================== 辅助函数 ==================
def log(msg):
    print(msg, flush=True)

def safe_print_error(prefix, error_obj):
    err_str = str(error_obj)
    if FMP_KEY:
        err_str = err_str.replace(FMP_KEY, "******")
    log(f"❌ {prefix}: {err_str}")

# ================== Google Translate ==================
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

# ================== 持久化存储 ==================
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

# ================== 更新 S&P 500 名单 ==================
async def update_sp500_list():
    global sp500_symbols
    log("🔄 正在更新 S&P 500 名单...")
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
                        log("⚠️ GitHub 数据异常")
                        sp500_symbols.update(FALLBACK_GIANTS)
                else:
                    log(f"⚠️ GitHub 访问失败: {resp.status}")
                    sp500_symbols.update(FALLBACK_GIANTS)
        except Exception as e:
            safe_print_error("更新名单失败", e)
            sp500_symbols.update(FALLBACK_GIANTS)

# ================== 经济日历 (FMP) ==================
async def fetch_us_events(target_date_str, min_importance=2):
    # 保持原样，宏观日历FMP做得很好
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

# ================== 财报获取 (Yahoo 爬虫版) ==================
async def fetch_earnings(date_str):
    if not sp500_symbols: await update_sp500_list()
    
    log(f"🔍 [调试] 爬取 Yahoo 财报: {date_str}")
    
    # Yahoo 每次只返回 100 条，如果当天财报多，需要翻页。
    # 考虑到我们只关心热门股，爬前 200 条通常够了。
    important_stocks = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    async with aiohttp.ClientSession() as session:
        for offset in [0, 100]: # 爬两页
            url = f"{YAHOO_CAL_URL}?day={date_str}&offset={offset}&size=100"
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status != 200:
                        log(f"❌ Yahoo 访问失败: {resp.status}")
                        break
                    
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # 找到表格行
                    rows = soup.find_all('tr')
                    
                    # Yahoo 表格结构通常是: Symbol | Company | Call Time | EPS Estimate | Reported EPS | Surprise
                    # 但 "Call Time" 有时候是第三列，有时候显示为 "Time"
                    # 我们遍历 td 找文本
                    
                    for row in rows:
                        cols = row.find_all('td')
                        if not cols: continue
                        
                        # 提取 Symbol (第一列)
                        symbol_tag = cols[0].find('a')
                        if not symbol_tag: continue
                        symbol = symbol_tag.text.strip()
                        
                        # 提取 Time (通常是第三列，index 2)
                        # 格式: "After Market Close", "Before Market Open", "Time Not Supplied"
                        time_text = "Unknown"
                        if len(cols) > 2:
                            time_text = cols[2].text.strip()
                        
                        # === 筛选逻辑 ===
                        is_hot = symbol in HOT_STOCKS
                        is_sp500 = symbol in sp500_symbols
                        
                        if is_hot or is_sp500:
                            # 转换时间代码
                            time_code = 'other'
                            if "Before" in time_text: time_code = 'bmo'
                            elif "After" in time_text: time_code = 'amc'
                            
                            important_stocks.append({
                                'symbol': symbol,
                                'time': time_code,
                                'is_hot': is_hot
                            })
                            
            except Exception as e:
                safe_print_error("Yahoo Parse Error", e)
                continue
            
            await asyncio.sleep(0.5) # 礼貌爬虫

    log(f"✅ 筛选后剩余 {len(important_stocks)} 家")
    
    # 去重 (因为可能翻页重复)
    unique_stocks = {s['symbol']: s for s in important_stocks}.values()
    sorted_stocks = sorted(unique_stocks, key=lambda x: x['is_hot'], reverse=True)

    result = {'bmo': [], 'amc': [], 'other': []}
    for stock in sorted_stocks:
        entry = f"**{stock['symbol']}**"
        if stock['is_hot']: entry += " 🔥"
        
        t = stock['time']
        if t == 'bmo': result['bmo'].append(entry)
        elif t == 'amc': result['amc'].append(entry)
        else: result['other'].append(entry)
        
    return result

# ================== 格式化 Embed ==================
def format_calendar_embed(events, date_str, min_imp):
    title = f"📅 今日宏观 ({date_str})"
    if not events: return [discord.Embed(title=title, description="无重要事件", color=0x3498db)]
    embed = discord.Embed(title=title, color=0x3498db)
    for e in events:
        val = f"影响: {e['importance']}" if any(k in e['orig_title'] for k in SPEECH_KEYWORDS) else \
              f"影响: {e['importance']} | 预期: {e['forecast']} | 前值: {e['previous']}"
        embed.add_field(name=f"{e['time']} {e['title']}", value=val, inline=False)
    return [embed]

def format_earnings_embed(data, date_str):
    if not data or not any(data.values()): return None
    title = f"💰 重点财报 ({date_str})"
    embed = discord.Embed(title=title, description="数据来源: Yahoo Finance", color=0xf1c40f)
    
    def add_section(name, items):
        if not items: return
        content = ""
        for item in items:
            if len(content) + len(item) + 50 > 900:
                content += f"\n...以及其他 {len(items) - items.index(item)} 家"
                break
            content += item + "\n"
        embed.add_field(name=name, value=content, inline=False)

    add_section("☀️ 盘前 (Before Open)", data.get('bmo'))
    add_section("🌙 盘后 (After Close)", data.get('amc'))
    add_section("🕒 时间未定 / 盘中", data.get('other'))
    return embed

# ================== 定时任务 ==================
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

# ================== 启动 ==================
@bot.event
async def on_ready():
    load_settings()
    log(f'✅ Bot 已登录: {bot.user}')
    await bot.tree.sync()
    await update_sp500_list()
    if not main_loop.is_running(): main_loop.start()

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
    data = await fetch_earnings(date)
    embed = format_earnings_embed(data, date)
    if embed: await interaction.followup.send(embed=embed)
    else: await interaction.followup.send(f"📅 **{date}** 无重点财报", ephemeral=True)

@bot.tree.command(name="test_push", description="测试宏观日历")
async def test_push(interaction: discord.Interaction):
    await interaction.response.defer()
    today = datetime.datetime.now(BJT).strftime("%Y-%m-%d")
    evts = await fetch_us_events(today, 2)
    for em in format_calendar_embed(evts, today, 2): await interaction.followup.send(embed=em)

if __name__ == "__main__":
    bot.run(TOKEN)
