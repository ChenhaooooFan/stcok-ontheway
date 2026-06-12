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
# 未匹配 SKU 分类（先算，供对账表使用）：
#   🟢 新款：数量为标准首单量 S=60/M=80/L=40，且 SKU 不在库存表中
#   🔴 需检查：不在库存表中，但数量不符合新款标准首单量
# ---------------------------------------------------------------------------

unmatched = {k: v for k, v in combined.items() if k not in matched_keys}

by_sku: dict = {}
for (sku, size), qty in unmatched.items():
    by_sku.setdefault(sku, {})[size] = qty

NEW_STYLE_PATTERN = {"S": 60, "M": 80, "L": 40}
new_rows, check_rows = [], []
for sku in sorted(by_sku):
    sizes = by_sku[sku]
    row = {
        "主 SKU": sku,
        "S": sizes.get("S", 0),
        "M": sizes.get("M", 0),
        "L": sizes.get("L", 0),
    }
    row["合计"] = row["S"] + row["M"] + row["L"]
    if {k: sizes.get(k, 0) for k in "SML"} == NEW_STYLE_PATTERN:
        row["状态"] = "🟢 新款"
        new_rows.append(row)
    else:
        row["状态"] = "🔴 需检查"
        check_rows.append(row)

new_total = sum(r["合计"] for r in new_rows)
check_total = sum(r["合计"] for r in check_rows)

# ---------------------------------------------------------------------------
# 总量对账：提取数量（源文件） vs 加入数量（生成总表），新款计入对账
# ---------------------------------------------------------------------------

st.subheader("🔍 总量对账（源文件 vs 生成总表）")

src_instock = int(to_num(stock_df["In_stock"]).sum())
out_instock = int(result["In_stock"].sum())

src_order = sum(order_lookup.values())
src_transit = sum(transit_lookup.values())
src_otw = src_order + src_transit
out_otw = int(total_otw)

otw_diff = src_otw - out_otw
otw_adjusted = otw_diff - new_total  # 扣除已识别新款后的剩余差额

if otw_diff == 0:
    otw_status = "✅ 一致"
elif otw_adjusted == 0:
    otw_status = f"🟢 一致（差额 {otw_diff} 全部为新款）"
else:
    otw_status = f"⚠️ 不一致（扣除新款后仍差 {otw_adjusted}）"

recon = pd.DataFrame(
    {
        "项目": ["在仓 In_stock", "在途 On_the_way"],
        "提取数量（源文件）": [src_instock, src_otw],
        "加入数量（生成总表）": [out_instock, out_otw],
        "差额": [src_instock - out_instock, otw_diff],
        "其中新款": [0, new_total],
        "扣除新款后差额": [src_instock - out_instock, otw_adjusted],
        "是否一致": [
            "✅ 一致" if src_instock == out_instock else "⚠️ 不一致",
            otw_status,
        ],
    }
)
st.dataframe(recon, use_container_width=True, hide_index=True)
st.caption(
    f"在途提取明细：订购总表 {src_order} + 出货单 {src_transit} = {src_otw}"
    + (f" ｜ 已识别新款 {len(new_rows)} 款共 {new_total} 件" if new_rows else "")
)

if otw_diff != 0 and otw_adjusted != 0:
    st.warning(
        f"扣除新款后在途仍有 {otw_adjusted} 件差额，"
        "对应下方【🔴 需检查】SKU，请人工核对。"
    )

st.dataframe(result, use_container_width=True, height=520)

# ---------------------------------------------------------------------------
# 未匹配 SKU 明细展示（新款标绿 / 异常标红）
# ---------------------------------------------------------------------------

if unmatched:
    def _style_rows(df: pd.DataFrame):
        def color(row):
            bg = "#d9f2d9" if "新款" in row["状态"] else "#fde2e2"
            return [f"background-color: {bg}"] * len(row)
        return df.style.apply(color, axis=1)

    if new_rows:
        new_df = pd.DataFrame(new_rows)
        st.success(
            f"🟢 识别到 {len(new_rows)} 个新款（标准首单量 S60 / M80 / L40，"
            f"且未在库存表建档），合计 {new_total} 件，已计入上方对账。"
            "建议在库存表补建 SKU 后重新生成。"
        )
        st.dataframe(_style_rows(new_df), use_container_width=True, hide_index=True)

    if check_rows:
        check_df = pd.DataFrame(check_rows)
        st.error(
            f"🔴 {len(check_rows)} 个 SKU 不在库存表中、且数量不符合新款标准首单量，"
            f"合计 {check_total} 件，请人工核对（可能是 SKU 拼写错误或漏建档）。"
        )
        st.dataframe(_style_rows(check_df), use_container_width=True, hide_index=True)

csv_bytes = result.to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "📥 下载在仓在途总表 CSV",
    data=csv_bytes,
    file_name="在仓在途总表.csv",
    mime="text/csv",
    type="primary",
)
