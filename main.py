# ================== 核心逻辑：财报获取 (终极调试版) ==================
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

            # 2. 提取 Symbol
            symbols = list(set([item['symbol'] for item in calendar_data if item.get('symbol')]))
            print(f"✅ [调试] 原始名单共找到 {len(symbols)} 家公司")
            
            # --- 🕵️‍♂️ 专门侦查 NVDA ---
            if 'NVDA' in symbols:
                print(f"🎉 [调试] ！！！在原始名单中找到了 NVDA ！！！")
            else:
                print(f"❌ [调试] 原始名单里没有 NVDA。可能 FMP 把它放在了 20号 或者 21号？")
            # -----------------------

            # 3. 分批查询市值
            important_stocks = []
            chunk_size = 50 
            
            # 记录一下这一天遇到的最大市值，看看 API 到底有没有给数据
            daily_max_mcap = 0
            daily_max_symbol = "None"

            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                quote_url = f"{FMP_QUOTE_URL}{chunk_str}?apikey={FMP_KEY}"
                
                try:
                    async with session.get(quote_url, timeout=10) as q_resp:
                        if q_resp.status == 200:
                            quotes = await q_resp.json()
                            
                            if not quotes:
                                print(f"⚠️ [调试] Quote API 返回了空列表！Batch: {i}")
                                continue

                            for q in quotes:
                                mcap = q.get('marketCap', 0)
                                symbol = q['symbol']
                                
                                # 记录最大值用于排查
                                if mcap and mcap > daily_max_mcap:
                                    daily_max_mcap = mcap
                                    daily_max_symbol = symbol

                                # 特别关注 NVDA 的市值
                                if symbol == 'NVDA':
                                    print(f"📉 [调试] 读到 NVDA 市值: {mcap} (阈值: {MIN_MARKET_CAP})")

                                if mcap and mcap >= MIN_MARKET_CAP:
                                    important_stocks.append({
                                        'symbol': q['symbol'],
                                        'name': q.get('name', q['symbol']),
                                        'marketCap': mcap,
                                        'time': next((x['time'] for x in calendar_data if x['symbol'] == q['symbol']), 'bmo')
                                    })
                        else:
                            print(f"❌ [调试] Quote API 报错: {q_resp.status}")

                except Exception as e:
                    safe_print_error("Quote fetch error", e)
                    continue
                
                await asyncio.sleep(0.1)

            print(f"📊 [调试] 本日 ({date_str}) 扫描到的最大市值是: {daily_max_symbol} - {daily_max_mcap/100000000:.2f}亿")
            print(f"✅ [调试] 最终过滤后剩余 {len(important_stocks)} 家")

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
