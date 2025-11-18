import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
import os
import json
from datetime import datetime, time
import pytz

# ===== 环境变量 =====
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FMP_API_KEY = os.getenv("FMP_API_KEY")

# ===== 机器人 intents =====
intents = discord.Intents.default()
intents.message_content = False
bot = commands.Bot(command_prefix="!", intents=intents)

# ===== settings.json 保存路径 =====
SETTINGS_FILE = "/data/settings.json"

settings = {"servers": []}


# ======================================================
#                 载入 / 保存 settings.json
# ======================================================
def load_settings():
    global settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
        print(f"成功加载 settings.json（{len(settings['servers'])} 个服务器）")
    except FileNotFoundError:
        print("settings.json 不存在，将在首次保存时创建")
        settings = {"servers": []}
    except Exception as e:
        print(f"读取 settings.json 失败: {e}")
        settings = {"servers": []}


def save_settings():
    try:
        os.makedirs("/data", exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        print(f"settings.json 已保存到 {SETTINGS_FILE}")
    except Exception as e:
        print(f"保存 settings.json 失败: {e}")


# ======================================================
#                     查询股价
# ======================================================
def get_stock_price(symbol):
    url = f"https://financialmodelingprep.com/stable/quote-short?symbol={symbol}&apikey={FMP_API_KEY}"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()

        if not data:
            return None, None

        price = data[0].get("price")
        change = data[0].get("change")

        return price, change

    except Exception as e:
        print(f"查询股价失败: {e}")
        return None, None


# ======================================================
#                     心跳任务（每60秒）
# ======================================================
@tasks.loop(seconds=60)
async def heartbeat():
    beijing = datetime.now(pytz.timezone("Asia/Shanghai"))
    print(f"❤️ 心跳正常 - 北京时间 {beijing.strftime('%Y-%m-%d %H:%M:%S')}")


# ======================================================
#                 主推送任务（每天早上发送）
# ======================================================
@tasks.loop(time=time(9, 0, 0, tzinfo=pytz.timezone("Asia/Shanghai")))
async def daily_push():
    print("📢 开始执行 daily_push()")

    for info in settings["servers"]:
        guild_id = info.get("guild_id")
        channel_id = info.get("channel_id")
        symbol = info.get("symbol")

        channel = bot.get_channel(channel_id)
        if not channel:
            print(f"找不到频道: {guild_id}/{channel_id}")
            continue

        price, change = get_stock_price(symbol)

        if price is None:
            await channel.send(f"⚠️ 无法获取 {symbol} 股价，请稍后重试。")
            continue

        arrow = "🟢" if change > 0 else "🔴"

        await channel.send(
            f"📈 今日股价推送：{symbol}\n"
            f"价格：**${price}**\n"
            f"涨跌：{arrow} {change}"
        )

    print("📢 daily_push 执行完毕")


# ======================================================
#                 斜杠命令：绑定推送
# ======================================================
@bot.tree.command(name="bind", description="绑定当前频道每天推送某支股票价格")
@app_commands.describe(symbol="股票代码，例如 AAPL / TSLA")
async def bind(interaction: discord.Interaction, symbol: str):
    guild_id = interaction.guild.id
    channel_id = interaction.channel.id

    # 保存设置
    settings["servers"] = [
        s for s in settings["servers"] if s["guild_id"] != guild_id
    ]

    settings["servers"].append({
        "guild_id": guild_id,
        "channel_id": channel_id,
        "symbol": symbol.upper()
    })

    save_settings()

    await interaction.response.send_message(
        f"✅ 已绑定！\n服务器：{guild_id}\n频道：{channel_id}\n股票：{symbol.upper()}",
        ephemeral=True
    )


# ======================================================
#                   斜杠命令：查询
# ======================================================
@bot.tree.command(name="stock", description="查询股票价格")
@app_commands.describe(symbol="股票代码，例如 AAPL / TSLA")
async def stock(interaction: discord.Interaction, symbol: str):
    await interaction.response.defer()

    price, change = get_stock_price(symbol.upper())
    if price is None:
        await interaction.followup.send("⚠️ 无法获取股价，请稍后再试。")
        return

    arrow = "🟢" if change > 0 else "🔴"

    await interaction.followup.send(
        f"📌 {symbol.upper()}\n"
        f"价格：**${price}**\n"
        f"涨跌：{arrow} {change}"
    )


# ======================================================
#          setup_hook（官方推荐启动 tasks 的位置）
# ======================================================
@bot.event
async def setup_hook():
    print("setup_hook() 已执行")

    load_settings()

    # 同步斜杠命令
    try:
        synced = await bot.tree.sync()
        print(f"已同步 {len(synced)} 个斜杠命令")
    except Exception as e:
        print(f"命令同步失败: {e}")

    # 启动心跳
    if not heartbeat.is_running():
        heartbeat.start()
        print("heartbeat 已启动")

    # 启动每天推送任务
    if not daily_push.is_running():
        daily_push.start()
        print("daily_push 已启动")


# ======================================================
#                         启动
# ======================================================
bot.run(DISCORD_TOKEN)
