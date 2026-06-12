# -*- coding: utf-8 -*-
"""
NailVesta 在仓在途总表生成器
- 上传 3 个文件：库存表（在仓）、A链订购总表（在途①）、A链出货单（在途②）
- 通过主 SKU + 尺码(S/M/L) 对应，合并两个来源的在途数量到 On_the_way 列
- 输出格式与目标总表一致：Name, Seller SKU, In_stock, On_the_way
"""

import io
import re

import pandas as pd
import streamlit as st

st.set_page_config(page_title="NailVesta 在仓在途总表", page_icon="💅", layout="wide")

st.title("💅 NailVesta 在仓在途总表生成器")
st.caption("上传库存表 + 订购总表 + 出货单，自动生成带 On_the_way 列的总表")

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

SIZE_COL_MAP = {"S": "S数量", "M": "M数量", "L": "L数量"}


def read_csv_robust(uploaded_file) -> pd.DataFrame:
    """兼容 BOM / 不同编码地读取 CSV。"""
    raw = uploaded_file.getvalue()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            df = pd.read_csv(io.BytesIO(raw), encoding=enc)
            break
        except (UnicodeDecodeError, pd.errors.ParserError):
            continue
    else:
        raise ValueError(f"无法读取文件 {uploaded_file.name}，请检查编码/格式")
    # 去掉列名里的 BOM 和首尾空格
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    return df


def split_seller_sku(seller_sku: str):
    """'NPJ005-S' -> ('NPJ005', 'S')；无法解析时返回 (原值, None)。"""
    s = str(seller_sku).strip().upper()
    m = re.match(r"^(.*?)-([SML])$", s)
    if m:
        return m.group(1), m.group(2)
    return s, None


def to_num(series: pd.Series) -> pd.Series:
    """数量列安全转数值，空/异常按 0。"""
    return pd.to_numeric(series, errors="coerce").fillna(0)


def build_on_the_way_lookup(df: pd.DataFrame, source_name: str) -> dict:
    """
    把一个在途来源表（订购总表 或 出货单）汇总为:
        { (主SKU, 尺码): 数量之和 }
    主 SKU 优先取 'SKU' 列；若没有，则从 '库位关联 - S' 等列提取。
    同一 SKU 多行（多批次/重复下单）自动累加。
    """
    df = df.copy()

    # 1) 确定主 SKU 列
    sku_series = None
    if "SKU" in df.columns:
        sku_series = df["SKU"].astype(str).str.strip().str.upper()
    else:
        # 从库位关联列提取，如 'NPX007-S' -> 'NPX007'
        for col in df.columns:
            if "库位关联" in col:
                sku_series = (
                    df[col].astype(str).str.strip().str.upper()
                    .str.replace(r"-[SML]$", "", regex=True)
                )
                break
    if sku_series is None:
        st.error(f"【{source_name}】中找不到 'SKU' 列或 '库位关联' 列，无法对应款式。")
        return {}

    df["_main_sku"] = sku_series

    # 2) 检查尺码数量列
    missing = [c for c in SIZE_COL_MAP.values() if c not in df.columns]
    if missing:
        st.error(f"【{source_name}】缺少数量列：{missing}")
        return {}

    # 3) 汇总
    lookup: dict = {}
    grouped = df.groupby("_main_sku", dropna=False)
    for sku, g in grouped:
        if not sku or sku == "NAN":
            continue
        for size, col in SIZE_COL_MAP.items():
            qty = int(to_num(g[col]).sum())
            if qty:
                lookup[(sku, size)] = lookup.get((sku, size), 0) + qty
    return lookup


# ---------------------------------------------------------------------------
# 文件上传
# ---------------------------------------------------------------------------

col1, col2, col3 = st.columns(3)
with col1:
    stock_file = st.file_uploader("① 库存表（在仓）", type="csv", key="stock")
    st.caption("需含列：Name / Seller SKU / In_stock")
with col2:
    order_file = st.file_uploader("② A链订购总表（在途）", type="csv", key="order")
    st.caption("需含列：SKU / S数量 / M数量 / L数量")
with col3:
    transit_file = st.file_uploader("③ A链出货单（在途）", type="csv", key="transit")
    st.caption("含 SKU 或 库位关联 列 + S/M/L 数量")

if not (stock_file and order_file and transit_file):
    st.info("请上传全部 3 个文件后自动生成总表。")
    st.stop()

# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

stock_df = read_csv_robust(stock_file)
order_df = read_csv_robust(order_file)
transit_df = read_csv_robust(transit_file)

required_stock_cols = {"Name", "Seller SKU", "In_stock"}
if not required_stock_cols.issubset(stock_df.columns):
    st.error(f"库存表缺少列：{required_stock_cols - set(stock_df.columns)}")
    st.stop()

# 两个在途来源分别汇总，再合并相加
order_lookup = build_on_the_way_lookup(order_df, "订购总表")
transit_lookup = build_on_the_way_lookup(transit_df, "出货单")

combined: dict = {}
for lk in (order_lookup, transit_lookup):
    for key, qty in lk.items():
        combined[key] = combined.get(key, 0) + qty

# 按库存表逐行匹配
result = stock_df[["Name", "Seller SKU", "In_stock"]].copy()
result["In_stock"] = to_num(result["In_stock"]).astype(int)

on_the_way_vals = []
matched_keys = set()
for sku_full in result["Seller SKU"]:
    main_sku, size = split_seller_sku(sku_full)
    qty = combined.get((main_sku, size), 0) if size else 0
    if size:
        matched_keys.add((main_sku, size))
    # 与目标表一致：0 显示为空
    on_the_way_vals.append(int(qty) if qty else "")

result["On_the_way"] = on_the_way_vals

# ---------------------------------------------------------------------------
# 展示与下载
# ---------------------------------------------------------------------------

total_otw = sum(v for v in on_the_way_vals if v != "")
m1, m2, m3 = st.columns(3)
m1.metric("SKU 行数", len(result))
m2.metric("在仓总量", int(result["In_stock"].sum()))
m3.metric("在途总量", int(total_otw))

# ---------------------------------------------------------------------------
# 总量对账：提取数量（源文件） vs 加入数量（生成总表）
# ---------------------------------------------------------------------------

st.subheader("🔍 总量对账（源文件 vs 生成总表）")

src_instock = int(to_num(stock_df["In_stock"]).sum())
out_instock = int(result["In_stock"].sum())

src_order = sum(order_lookup.values())
src_transit = sum(transit_lookup.values())
src_otw = src_order + src_transit
out_otw = int(total_otw)

recon = pd.DataFrame(
    {
        "项目": ["在仓 In_stock", "在途 On_the_way"],
        "提取数量（源文件）": [src_instock, src_otw],
        "加入数量（生成总表）": [out_instock, out_otw],
        "差额": [src_instock - out_instock, src_otw - out_otw],
        "是否一致": [
            "✅ 一致" if src_instock == out_instock else "⚠️ 不一致",
            "✅ 一致" if src_otw == out_otw else "⚠️ 不一致",
        ],
    }
)
st.dataframe(recon, use_container_width=True, hide_index=True)
st.caption(f"在途提取明细：订购总表 {src_order} + 出货单 {src_transit} = {src_otw}")

if src_otw != out_otw:
    st.warning(
        f"在途差额 {src_otw - out_otw} 件：以下 SKU 在源文件中有在途量，"
        "但库存表中不存在（通常为未建档的新款），因此未加入总表。"
        "请在库存表中补建这些 SKU 后重新生成。"
    )

st.dataframe(result, use_container_width=True, height=520)

# 在途来源中存在、但库存表里没有的 SKU（新款还没入库的情况），提示出来
unmatched = {k: v for k, v in combined.items() if k not in matched_keys}
if unmatched:
    with st.expander(
        f"⚠️ {len(unmatched)} 个在途 SKU 在库存表中不存在（合计 {sum(unmatched.values())} 件，即上方差额来源）",
        expanded=(src_otw != out_otw),
    ):
        un_df = pd.DataFrame(
            [(f"{sku}-{size}", qty) for (sku, size), qty in sorted(unmatched.items())],
            columns=["Seller SKU", "On_the_way"],
        )
        st.dataframe(un_df, use_container_width=True)

csv_bytes = result.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "📥 下载在仓在途总表 CSV",
    data=csv_bytes,
    file_name="在仓在途总表.csv",
    mime="text/csv",
    type="primary",
)
