"""Controlled, explicitly synthetic business scenarios for the guided showcase."""
from copy import deepcopy
from datetime import date, timedelta

PERIODS = {"previous_start": "2026-09-14", "previous_end": "2026-09-20",
           "start": "2026-09-21", "end": "2026-09-27"}

SCENARIOS = {
    "coffee_orders": {
        "mode": "orders", "filename": "校园咖啡店-小票明细.csv", "name": "校园咖啡店 · 两周小票",
        "source": "项目内生成的中文模拟小票；2026年9月两周，人民币。用于理解与验收，不是真实商家经营数据。",
        "guide": {"id": "coffee_orders", "title": "先读懂数据：一家校园咖啡店的两周小票",
                  "summary": "店长想知道：第二周扣除退款后的净交易额少了多少，变化集中在哪些商品？先检查数据，再比较两个完整的周一至周日。",
                  "row_meaning": "一行是一张小票中的一个商品明细。本样本每张正向小票只有一个商品；数量为负的两行是模拟退款调整。",
                  "definitions": ["正向交易额：数量为正的收款合计。", "负向调整额：本样本只有退款，按负数保存。", "净交易额：收款加上负数退款；没有成本数据，不能当作利润。", "正向订单数：按正向小票编号去重；不等于杯数或顾客人数。"],
                  "caveat": "这是为方便手算而构造的样本，每天销量较规律；不能据此证明真实市场需求或经营原因。末尾另放两条错误记录用于质量检查，默认不计入指标。",
                  "periods": PERIODS,
                  "compare_label": "比较两周净交易额", "ranking_label": "查看商品收款排名"}},
    "coffee_content": {
        "mode": "content", "filename": "校园咖啡店-内容表现.csv", "name": "校园咖啡店 · 内容表现",
        "source": "项目内生成的中文模拟内容数据；与咖啡店小票共用日期，但没有用户归因关联，不能推导内容带来的订单。",
        "guide": {"id": "coffee_content", "title": "同一个场景：咖啡店的内容表现",
                  "summary": "比较两周的内容点击变化，再结合曝光规模看点击率。它与小票表没有用户、订单或转化关联。",
                  "row_meaning": "一行是某天某类内容的曝光与点击汇总；不是一位用户。不同内容的曝光可能来自同一个人。",
                  "definitions": ["曝光次数：内容被展示的次数，不能当作去重人数。", "点击次数：内容被点击的次数，不能当作购买订单。", "加权点击率：总点击除以总曝光；不能把每行百分比直接平均。"],
                  "caveat": "明确的模拟数据。没有广告费、转化或成本字段，不计算ROI，也不把点击上涨写成销售上涨。",
                  "periods": PERIODS, "compare_label": "比较两周点击次数", "ranking_label": "查看内容点击率排名"}}
}


def scenario_rows(key):
    """Deterministic fixture, with simple hand-checkable totals."""
    rows = []
    if key == "coffee_orders":
        for week in range(2):
            for day in range(7):
                when = (date(2026, 9, 14) + timedelta(days=week*7+day)).isoformat()
                for item, price, count in [("美式咖啡", 18, 3+week), ("拿铁咖啡", 26, 3-week),
                                           ("柠檬茶", 16, 3), ("芝士蛋糕", 22, 3)]:
                    for _ in range(count):
                        rows.append([when, item, 1, price, f"SALE-{len(rows)+1:04d}"])
        rows.extend([["2026-09-20", "拿铁咖啡", -1, 26, "REFUND-001"],
                     ["2026-09-27", "拿铁咖啡", -2, 26, "REFUND-002"],
                     ["日期待确认", "美式咖啡", 1, 18, "CHECK-001"],
                     ["2026-09-27", "柠檬茶", "未填写", 16, "CHECK-002"]])
        return ["日期", "商品", "数量", "单价", "订单编号"], rows
    if key == "coffee_content":
        for week in range(2):
            for day in range(7):
                when = (date(2026, 9, 14) + timedelta(days=week*7+day)).isoformat()
                for label, views, clicks in [("咖啡知识", 1000, 20+week*5),
                                             ("新品菜单", 300, 12-week*3),
                                             ("校园优惠", 600+week*300, 24+week*12)]:
                    rows.append([when, label, views, clicks, f"POST-{len(rows)+1:03d}"])
        return ["日期", "内容", "曝光次数", "点击次数", "内容编号"], rows
    raise ValueError("Unknown scenario")


def guide_for(key):
    return deepcopy(SCENARIOS[key]["guide"])
