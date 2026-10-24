"""Explicit synthetic fixtures, not customer outcomes."""
import csv
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parents[1]
(ROOT / "examples").mkdir(exist_ok=True)
rows = []
for month in (8, 9):
    for day in range(1, 29):
        for index, item in enumerate(("帆布包", "陶瓷杯", "笔记本", "桌面摆件")):
            quantity = (day * (index+2)) % 8 + 1
            if month == 9 and index == 0:
                quantity = max(1, quantity - 3)
            if month == 9 and index == 2:
                quantity += 2
            price = [39.9, 29.5, 16, 48][index]
            rows.append([f"2026-{month:02d}-{day:02d}", item, quantity, price, f"O{month}{day:02d}{index}"])
        if day % 7 == 0:
            rows.append([f"2026-{month:02d}-{day:02d}", "陶瓷杯", -1, 29.5, f"C{month}{day:02d}"])
rows += [["无效日期", "帆布包", 2, 39.9, "BAD1"], ["2026-09-15", "陶瓷杯", "未知", 29.5, "BAD2"], ["2026-09-17", "帆布包", 0, 39.9, "BAD3"], rows[0]]
content = [["2026-08-01", "产品教程", 10000, 250, "P01"], ["2026-08-02", "场景案例", 2000, 140, "P02"], ["2026-08-03", "新品介绍", 5000, 150, "P03"], ["2026-09-01", "产品教程", 12000, 360, "P04"], ["2026-09-02", "场景案例", 3000, 210, "P05"], ["2026-09-03", "新品介绍", 6000, 180, "P06"], ["2026-09-04", "零曝光样本", 0, 0, "P07"], ["2026-09-05", "错误样本", 20, 30, "BAD"]]
for name, header, records in [("orders", ["日期", "商品", "数量", "单价", "订单编号"], rows), ("content", ["日期", "内容", "曝光次数", "点击次数", "内容编号"], content)]:
    with (ROOT / "examples" / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f); writer.writerow(header); writer.writerows(records)
for name, header, record in [("订单模板", ["日期", "商品", "数量", "单价", "订单编号"], ["2026-09-01", "示例商品", "2", "29.90", "ORDER-001"]), ("内容模板", ["日期", "内容", "曝光次数", "点击次数", "内容编号"], ["2026-09-01", "示例内容", "1000", "30", "POST-001"])]:
    with (ROOT / "web" / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer=csv.writer(f); writer.writerow(header); writer.writerow(record)
print("Synthetic fixtures and upload templates created.")
