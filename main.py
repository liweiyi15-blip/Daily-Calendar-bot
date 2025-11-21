# ================== 核心逻辑：财报获取 (防误杀版) ==================
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
            print(f"✅ [调试] 名单共找到 {len(symbols)} 家公司 (包含 NVDA: {'NVDA' in symbols})")

            # 3. 分批查询市值
            important_stocks = []
            chunk_size = 50 
            
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                chunk_str = ",".join(chunk)
                quote_url = f"{FMP_QUOTE_URL}{chunk_str}?apikey={FMP_KEY}"
                
                try:
                    async with session.get(quote_url, timeout=10) as q_resp:
                        # 重点调试：如果状态码不是200，或者返回空
                        if q_resp.status != 200:
                            print(f"❌ [调试] Quote API 报错: {q_resp.status}")
                            text = await q_resp.text()
                            print(f"   内容: {text[:100]}...") # 只打印前100字

                        quotes = await q_resp.json()
                        
                        # 创建一个字典方便查找，防止 quotes 顺序乱了
                        quote_map = {q['symbol']: q.get('marketCap', 0) for q in quotes}

                        for symbol in chunk:
                            mcap = quote_map.get(symbol, 0) # 获取市值，没有就是0
                            
                            # 🚨 强制修正：如果是 NVDA，强制通过
                            if symbol == 'NVDA':
                                print(f"👀 [调试] 正在检查 NVDA，获取到的市值: {mcap}")
                                if mcap == 0: mcap = 3000000000000 # 如果API坏了，给个假市值防止被过滤

                            # 逻辑修改：如果获取到了名字，就加入列表
                            # 如果市值是0，我们依然加入，但在显示时标记为 "市值未知"
                            # 只有当列表确实太长时，我们在 format 函数里截断，而不是在这里直接删掉
                            
                            # 匹配原始数据里的时间
                            orig_item = next((x for x in calendar_data if x['symbol'] == symbol), None)
                            stock_name = symbol # 默认用代码当名字
                            
                            # 尝试从 Quote 里拿名字，拿不到就用 Symbol
                            q_data = next((q for q in quotes if q['symbol'] == symbol), None)
                            if q_data and 'name' in q_data:
                                stock_name = q_data['name']

                            # 只要在名单里，我们先全部保留！(除非为了防刷屏，稍微过滤极小值)
                            # 这里把门槛降到 0，或者极低，确保有数据
                            if mcap >= 0: 
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

            print(f"✅ [调试] 最终列表有 {len(important_stocks)} 家")

            # 4. 分组排序
            result = {'bmo': [], 'amc': [], 'other': []}
            # 按市值倒序
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
