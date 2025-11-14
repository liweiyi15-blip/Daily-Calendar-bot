# main.py
import discord
from discord.ext import commands, tasks
import requests
import datetime
import pytz
import json
import os

# ================== 配置区 ==================
TOKEN = os.getenv('TOKEN') or 'YOUR_BOT_TOKEN_HERE'  # 从环境变量读取，Railway 设置
SETTINGS_FILE = 'settings.json'  # 持久化设置文件

intents = discord.Intents.default()
intents.message_content = True  # 启用消息内容
bot = commands.Bot(command_prefix='!', intents=intents)

# 时区
ET = pytz.timezone('America/New_York')
BJT = pytz.timezone('Asia/Shanghai')

# Investing.com API（无需密钥）
API_URL = "https://api.investing.com/api/financialdata/calendar"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.investing.com/economic-calendar/",
    "Origin": "https://www.investing.com"
}

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
    """拉取指定日期的事件（YYYY-MM-DD 格式）"""
    try:
        target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date
