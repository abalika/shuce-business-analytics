"""Accounting composition, not causal attribution. Money stays in integer cents."""
from decimal import Decimal, ROUND_HALF_UP


def money_summary(frame):
    positive = int(frame.positive_amount.sum())
    negative = int(frame.negative_amount.sum())
    return {"positive_cents": positive, "negative_cents": negative, "net_cents": positive+negative}


def category_summary(frame):
    summary = money_summary(frame)
    quantities = frame.quantity.map(lambda q: Decimal(str(q)))
    positive = frame[quantities > 0]
    negative = frame[quantities < 0]
    positive_quantity = sum((Decimal(str(q)) for q in positive.quantity), Decimal(0))
    negative_quantity = -sum((Decimal(str(q)) for q in negative.quantity), Decimal(0))
    # Rounded transaction amount / quantity, not the simple mean of row prices.
    weighted_price = (Decimal(summary["positive_cents"])/100/positive_quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if positive_quantity else None
    summary.update(positive_quantity=str(positive_quantity), negative_quantity=str(negative_quantity),
                   weighted_positive_price=str(weighted_price) if weighted_price is not None else None,
                   record_count=len(frame))
    return summary


def difference(current, previous):
    return {key: current[key]-previous[key] for key in ("positive_cents", "negative_cents", "net_cents")}


def change_breakdown(previous, current, contributions, limit=6):
    """Explain the total and largest category deltas without changing their original evidence."""
    # Legacy frames without quantities can still be read; don't invent an explanation.
    if not {"amount", "positive_amount", "negative_amount", "quantity"}.issubset(current.columns):
        return None
    a, b = money_summary(current), money_summary(previous)
    selected = contributions[:limit]
    dimensions = {row["dimension"] for row in selected}
    current_groups = {str(d): g for d, g in current[current.dimension.isin(dimensions)].groupby("dimension")}
    previous_groups = {str(d): g for d, g in previous[previous.dimension.isin(dimensions)].groupby("dimension")}
    empty = current.iloc[:0]
    categories = []
    for row in selected:
        ac = category_summary(current_groups.get(row["dimension"], empty))
        bc = category_summary(previous_groups.get(row["dimension"], empty))
        categories.append({"dimension": row["dimension"], "evidence_id": row["evidence_id"],
                           "current": ac, "previous": bc, "change": difference(ac, bc)})
    return {"current": a, "previous": b, "change": difference(a, b), "categories": categories,
            "shown_categories": len(categories), "all_categories": len(contributions),
            "note": "净交易额变化＝正向交易额变化＋负向调整额变化。下方只展示变化绝对值最大的分类；整体构成使用全部有效记录。数量仅在同一分类口径下参考，混合商品规格时不能直接解释销量；加权成交单价按正向金额÷正向数量计算，不是标价。负数的业务含义需按来源核查，这个拆分不证明经营原因。"}


def report_breakdown(result):
    import html
    b = result.get("change_breakdown")
    if not b:
        return ""
    esc = html.escape
    currency = esc(result["currency"])
    def money(cents):
        return f"{Decimal(cents)/100:,.2f} {currency}"
    def price(v):
        return "不可计算" if v is None else esc(v)+" "+currency
    rows = "".join(f"<tr><td>{esc(c['dimension'])} [{esc(c['evidence_id'])}]</td><td>{money(c['change']['positive_cents'])}</td><td>{money(c['change']['negative_cents'])}</td><td>{esc(c['previous']['positive_quantity'])} → {esc(c['current']['positive_quantity'])}</td><td>{esc(c['previous']['negative_quantity'])} → {esc(c['current']['negative_quantity'])}</td><td>{price(c['previous']['weighted_positive_price'])} → {price(c['current']['weighted_positive_price'])}</td></tr>" for c in b["categories"])
    change = b["change"]
    return f"<h2>金额变化构成（确定性计算）</h2><p>正向交易额变化 {money(change['positive_cents'])} ＋ 负向调整额变化 {money(change['negative_cents'])} ＝ 净交易额变化 {money(change['net_cents'])}</p><div style='overflow-x:auto'><table><tr><th>分类/证据</th><th>正向金额变化</th><th>负向调整变化</th><th>正向数量（基期→本期）</th><th>负向数量绝对值（基期→本期）</th><th>加权成交单价（基期→本期）</th></tr>{rows}</table></div><p class='small'>展示 {b['shown_categories']} / {b['all_categories']} 个分类。{esc(b['note'])}</p>"
