# ================== 核心逻辑：财报获取 (带调试日志版) ==================
async def fetch_earnings(date_str):
    print(f"🔍 [调试] 开始查询 {date_str} 的财报...")
    params = {"from": date_str, "to": date_str, "apikey": FMP_KEY}
    
    async with aiohttp.ClientSession() as session:
        try:
            # 1. 获取财报名单
            async with session.get(FMP_EARNINGS_URL, params=params, timeout=10) as resp:
                resp.raise_for_status()
                calendar_data = await resp.json()
            
            if not calendar_data:
                print(f"⚠️ [调试] FMP 返回了空列表，日期: {date_str}")
                return {}

            print(f"✅ [调试] 原始名单共找到 {len(calendar_data)} 家公司")

            # 2. 提取 Symbol
            symbols = list(set([item['symbol'] for item in calendar_data if item.get('symbol')]))
            # 打印前5个看看长啥样
            print(f"   [调试] 股票代码示例: {symbols[:5]}")

            # 3. 分批查询市值
            important_stocks = []
            chunk_size = 50 
            
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                quote_url = f"{FMP_QUOTE_URL}{chunk_str}?apikey={FMP_KEY}"
                
                try:
                    async with session.get(quote_url, timeout=10) as q_resp:
                        if q_resp.status == 200:
                            quotes = await q_resp.json()
                            # 打印每一批的查询结果概览
                            # print(f"   [调试] 成功获取 {len(quotes)} 个报价")
                            
                            for q in quotes:
                                mcap = q.get('marketCap', 0)
                                symbol = q['symbol']
                                
                                # --- 调试关键点 ---
                                # 如果是大公司 (比如市值 > 10亿)，打印出来看看
                                if mcap > 1_000_000_000:
                                    print(f"   [调试] 发现大市值: {symbol} - ${mcap/100000000:.2f}亿")
                                
                                if mcap and mcap >= MIN_MARKET_CAP:
                                    important_stocks.append({
                                        'symbol': q['symbol'],
                                        'name': q.get('name', q['symbol']),
                                        'marketCap': mcap,
                                        'time': next((x['time'] for x in calendar_data if x['symbol'] == q['symbol']), 'bmo')
                                    })
                except Exception as e:
                    safe_print_error("Quote fetch error", e)
                    continue
                
                await asyncio.sleep(0.1)

            print(f"✅ [调试] 过滤后剩余 {len(important_stocks)} 家 (阈值: {MIN_MARKET_CAP/100000000}亿)")

            # 4. 分组排序
            result = {'bmo': [], 'amc': [], 'other': []}
            important_stocks.sort(key=lambda x: x['marketCap'], reverse=True)

            for stock in important_stocks:
                time_code = stock['time'].lower()
                entry = f"**{stock['symbol']}** - {stock['name']}"
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
