---
name: research-analyst
description: 研究分析任务：先联网搜索收集数据，再自动写分析代码并执行验证，输出结论。触发词：分析股票、分析行情、研究、调研、数据分析、量化、回测、A股、美股、加密货币价格、市场分析
keywords: 分析 股票 行情 研究 调研 数据分析 量化 回测 A股 美股 加密货币 市场 价格走势 财务 基本面 技术分析 ETF 基金 期货
---

# 研究分析工作流

## 强制执行顺序：搜索 → 获取数据 → 写代码 → 运行验证 → 输出结论

**绝不允许** 直接给出未经验证的分析结论。所有数字必须来自代码运行结果。

---

## Phase 1：信息搜索

根据分析目标，先搜索背景信息：

```python
# 搜索顺序（用 web_fetch 工具）
# 股票/加密货币：
# 1. https://cn.bing.com/search?q=<股票代码>+股价+分析&ensearch=0
# 2. https://finance.yahoo.com/quote/<TICKER>
# 3. https://www.investing.com/search/?q=<name>

# A股专用：
# https://xueqiu.com/S/<代码>
# https://cn.bing.com/search?q=<代码>+实时行情

# 加密货币：
# https://coinmarketcap.com/currencies/<name>/
# https://www.coingecko.com/en/coins/<name>
```

搜索目标：
- 当前价格 / 最近涨跌
- 基本面（市值、PE、营收等，股票）
- 近期重大事件、新闻
- 行业背景

---

## Phase 2：数据获取代码

用 `execute_shell` 运行 Python 获取真实数据：

```python
# 安装依赖（如需要）
pip3 install yfinance pandas numpy matplotlib requests --break-system-packages -q

# 美股/ETF 数据获取
import yfinance as yf
import pandas as pd

ticker = yf.Ticker("AAPL")
hist   = ticker.history(period="6mo")
info   = ticker.info

print(f"当前价格: {info.get('currentPrice', 'N/A')}")
print(f"市值: {info.get('marketCap', 'N/A'):,}")
print(f"PE: {info.get('trailingPE', 'N/A')}")
print(hist.tail(10))
```

```python
# A股数据（备用方案，用 akshare）
pip3 install akshare --break-system-packages -q

import akshare as ak
df = ak.stock_zh_a_hist(symbol="600036", period="daily", start_date="20240101")
print(df.tail(10))
```

```python
# 加密货币数据
import requests
r = requests.get("https://api.coingecko.com/api/v3/simple/price",
                 params={"ids": "bitcoin", "vs_currencies": "usd,cny",
                         "include_24hr_change": "true"})
print(r.json())
```

---

## Phase 3：分析代码（运行后才能引用数字）

```python
import pandas as pd
import numpy as np

# 技术指标计算
def calc_indicators(df):
    # 移动均线
    df['MA5']  = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()

    # RSI
    delta = df['Close'].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = df['Close'].ewm(span=12).mean()
    ema26 = df['Close'].ewm(span=26).mean()
    df['MACD']   = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9).mean()

    # 布林带
    sma = df['Close'].rolling(20).mean()
    std = df['Close'].rolling(20).std()
    df['BB_upper'] = sma + 2*std
    df['BB_lower'] = sma - 2*std

    # 波动率（年化）
    df['Volatility'] = df['Close'].pct_change().rolling(20).std() * np.sqrt(252) * 100

    return df

df = calc_indicators(hist)
latest = df.iloc[-1]

print("=== 技术分析结果 ===")
print(f"最新收盘: {latest['Close']:.2f}")
print(f"MA5:  {latest['MA5']:.2f}  MA20: {latest['MA20']:.2f}  MA60: {latest['MA60']:.2f}")
print(f"RSI(14): {latest['RSI']:.1f}  {'超买' if latest['RSI']>70 else '超卖' if latest['RSI']<30 else '正常'}")
print(f"MACD: {latest['MACD']:.4f}  Signal: {latest['Signal']:.4f}")
print(f"布林带: [{latest['BB_lower']:.2f}, {latest['BB_upper']:.2f}]")
print(f"年化波动率: {latest['Volatility']:.1f}%")

# 近期涨跌
ret_1w  = (df['Close'].iloc[-1] / df['Close'].iloc[-5]  - 1) * 100
ret_1m  = (df['Close'].iloc[-1] / df['Close'].iloc[-20] - 1) * 100
ret_3m  = (df['Close'].iloc[-1] / df['Close'].iloc[-60] - 1) * 100
print(f"近1周: {ret_1w:+.2f}%  近1月: {ret_1m:+.2f}%  近3月: {ret_3m:+.2f}%")
```

---

## Phase 4：生成图表（可选）

```python
import matplotlib
matplotlib.use('Agg')   # 无图形界面时必加
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

fig, axes = plt.subplots(3, 1, figsize=(12, 10))
fig.suptitle(f'{ticker_name} 技术分析', fontsize=14)

# 价格 + 均线
ax1 = axes[0]
ax1.plot(df.index, df['Close'], label='Price', linewidth=1.5)
ax1.plot(df.index, df['MA5'],  label='MA5',  linestyle='--', alpha=0.7)
ax1.plot(df.index, df['MA20'], label='MA20', linestyle='--', alpha=0.7)
ax1.fill_between(df.index, df['BB_lower'], df['BB_upper'], alpha=0.1, label='BB')
ax1.legend(loc='upper left', fontsize=8)
ax1.set_ylabel('Price')

# RSI
ax2 = axes[1]
ax2.plot(df.index, df['RSI'], color='purple')
ax2.axhline(70, color='red',   linestyle='--', alpha=0.5)
ax2.axhline(30, color='green', linestyle='--', alpha=0.5)
ax2.set_ylim(0, 100)
ax2.set_ylabel('RSI')

# MACD
ax3 = axes[2]
ax3.plot(df.index, df['MACD'],   label='MACD',   color='blue')
ax3.plot(df.index, df['Signal'], label='Signal', color='orange')
macd_hist = df['MACD'] - df['Signal']
ax3.bar(df.index, macd_hist,
        color=['green' if v >= 0 else 'red' for v in macd_hist], alpha=0.5)
ax3.legend(fontsize=8)
ax3.set_ylabel('MACD')

plt.tight_layout()
plt.savefig('/tmp/analysis.png', dpi=150, bbox_inches='tight')
print("图表已保存: /tmp/analysis.png")
```

---

## Phase 5：量化回测模板

```python
# 简单均线策略回测
def backtest_ma_strategy(df, short=5, long=20, initial_capital=100000):
    df = df.copy()
    df['Signal'] = 0
    df.loc[df[f'MA{short}'] > df[f'MA{long}'], 'Signal'] = 1   # 多头
    df.loc[df[f'MA{short}'] < df[f'MA{long}'], 'Signal'] = -1  # 空头

    df['Position'] = df['Signal'].shift(1).fillna(0)
    df['Returns'] = df['Close'].pct_change()
    df['Strategy'] = df['Position'] * df['Returns']

    # 统计
    total_ret    = (1 + df['Strategy']).prod() - 1
    annual_ret   = (1 + total_ret) ** (252 / len(df)) - 1
    sharpe       = df['Strategy'].mean() / df['Strategy'].std() * np.sqrt(252)
    max_dd       = (df['Strategy'].cumsum() - df['Strategy'].cumsum().cummax()).min()
    win_rate     = (df['Strategy'] > 0).sum() / (df['Strategy'] != 0).sum()

    print(f"=== 回测结果 (MA{short}/MA{long}) ===")
    print(f"总收益: {total_ret*100:.2f}%")
    print(f"年化收益: {annual_ret*100:.2f}%")
    print(f"夏普比率: {sharpe:.2f}")
    print(f"最大回撤: {max_dd*100:.2f}%")
    print(f"胜率: {win_rate*100:.1f}%")

    return df

result = backtest_ma_strategy(df)
```

---

## 输出规范

分析完成后必须按此格式输出：

```
## [标的名称] 分析报告 [日期]

### 数据来源
- 数据时间范围：XXXX-XX-XX 至 XXXX-XX-XX
- 数据来源：yfinance / akshare / API

### 当前状态
- 价格：XXX（来自代码运行结果）
- 涨跌：近1周 X.XX% / 近1月 X.XX%

### 技术面
- MA趋势：多头 / 空头 / 震荡
- RSI：XX（超买/正常/超卖）
- MACD：金叉 / 死叉 / 中性

### 结论与风险提示
[基于数据的客观分析]

**免责声明：以上分析仅供参考，不构成投资建议。投资有风险，决策需谨慎。**
```
