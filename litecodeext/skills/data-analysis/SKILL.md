---
name: data-analysis
description: >
  Python 数据分析技能。使用 pandas/numpy/matplotlib/seaborn 进行数据清洗、
  统计分析、可视化、趋势预测。支持 CSV/JSON/Excel/SQL 数据源。
  触发关键词：数据分析、统计、图表、可视化、pandas、matplotlib、趋势、
  相关性、回归、分布、均值、方差、中位数、百分位、直方图、折线图、散点图、
  热力图、数据清洗、缺失值、异常值、聚合、分组、pivot、报表。
  遇到任何数据处理和分析任务必须使用本技能。

## Keywords
数据分析 统计 图表 可视化 pandas matplotlib numpy seaborn plotly
趋势 相关性 回归 分布 均值 方差 中位数 百分位 聚合 分组
直方图 折线图 散点图 热力图 柱状图 饼图 箱线图
csv json excel sql 数据清洗 缺失值 异常值 pivot 透视表 报表
dataframe series groupby merge join concat filter sort
---

# Data Analysis Skill

## 环境准备
```bash
pip3 install pandas numpy matplotlib seaborn openpyxl --break-system-packages
```

## 标准工作流模板

### 1. 数据加载
```python
import pandas as pd
import numpy as np

# CSV
df = pd.read_csv("data.csv", encoding="utf-8")
# Excel
df = pd.read_excel("data.xlsx", sheet_name=0)
# JSON
df = pd.read_json("data.json")
# SQL
# import sqlite3
# conn = sqlite3.connect("db.sqlite3")
# df = pd.read_sql("SELECT * FROM table_name", conn)

# 快速概览
print(f"形状: {df.shape}")
print(f"列: {list(df.columns)}")
print(f"类型:\n{df.dtypes}")
print(f"缺失值:\n{df.isnull().sum()}")
print(df.describe())
```

### 2. 数据清洗
```python
# 去重
df = df.drop_duplicates()

# 处理缺失值
df["col"] = df["col"].fillna(0)              # 填充0
df["col"] = df["col"].fillna(df["col"].mean())  # 填充均值
df = df.dropna(subset=["critical_col"])       # 删除关键列缺失的行

# 类型转换
df["date"] = pd.to_datetime(df["date"])
df["price"] = pd.to_numeric(df["price"], errors="coerce")

# 异常值过滤（IQR法）
Q1, Q3 = df["value"].quantile([0.25, 0.75])
IQR = Q3 - Q1
df = df[(df["value"] >= Q1 - 1.5*IQR) & (df["value"] <= Q3 + 1.5*IQR)]
```

### 3. 分析模板
```python
# 分组聚合
summary = df.groupby("category").agg(
    count=("id", "count"),
    mean_price=("price", "mean"),
    total_volume=("volume", "sum"),
).round(2).sort_values("total_volume", ascending=False)

# 时间序列
df.set_index("date", inplace=True)
daily = df.resample("D").agg({"price": "mean", "volume": "sum"})
weekly = df.resample("W").agg({"price": "mean", "volume": "sum"})

# 相关性矩阵
corr = df[["price", "volume", "market_cap"]].corr()

# 移动平均
df["MA7"] = df["price"].rolling(7).mean()
df["MA30"] = df["price"].rolling(30).mean()
```

### 4. 可视化
```python
import matplotlib
matplotlib.use("Agg")  # 无GUI环境必须
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 折线图
fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(df.index, df["price"], label="Price", linewidth=1.5)
ax.plot(df.index, df["MA7"], label="MA7", linestyle="--")
ax.set_title("价格趋势")
ax.legend()
fig.savefig("trend.png", dpi=150, bbox_inches="tight")
plt.close()

# 多子图
fig, axes = plt.subplots(2, 2, figsize=(14, 10))
df["price"].hist(ax=axes[0,0], bins=30); axes[0,0].set_title("价格分布")
df["volume"].plot(ax=axes[0,1]); axes[0,1].set_title("交易量")
df.boxplot(column="price", by="category", ax=axes[1,0]); axes[1,0].set_title("分类箱线图")
corr.plot(kind="bar", ax=axes[1,1]); axes[1,1].set_title("相关性")
fig.tight_layout()
fig.savefig("dashboard.png", dpi=150)
plt.close()
```

## 输出规范
- 图表保存为 PNG，放在 workspace 目录
- 数值结果保留2位小数
- 大数据集只展示 head(10) + describe()
- 分析结论用中文，附原始数据路径
