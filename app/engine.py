"""Deterministic evidence engine. A model never calculates or executes SQL."""
from __future__ import annotations

import io
import csv
import json
import re
from datetime import date as calendar_date
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any
import pandas as pd
from .breakdown import change_breakdown

MAX_ROWS = 600_000
SCHEMAS = {
    "orders": {"date": "日期", "dimension": "商品/分类", "quantity": "数量", "price": "单价", "entity": "订单编号（可选）"},
    "content": {"date": "日期", "dimension": "内容/渠道", "views": "曝光次数", "clicks": "点击次数", "entity": "内容编号（可选）"},
}
ALIASES = {
    "date": ["日期", "时间", "date", "InvoiceDate", "发布时间"],
    "dimension": ["商品", "商品名称", "分类", "渠道", "内容", "标题", "dimension", "Description", "StockCode"],
    "quantity": ["数量", "quantity", "Quantity"],
    "price": ["单价", "price", "UnitPrice"],
    "entity": ["订单编号", "内容编号", "entity", "InvoiceNo", "ID"],
    "views": ["曝光", "曝光次数", "展现量", "views", "impressions"],
    "clicks": ["点击", "点击次数", "clicks"],
}


class AnalysisError(ValueError):
    pass


def read_frame(blob: bytes, name: str) -> pd.DataFrame:
    if name.lower().endswith(".xlsx"):
        original_header = pd.read_excel(io.BytesIO(blob), header=None, nrows=1, keep_default_na=False).iloc[0].astype(str).str.strip().tolist()
        df = pd.read_excel(io.BytesIO(blob), dtype=str, keep_default_na=False, nrows=MAX_ROWS + 1)
    elif name.lower().endswith(".csv"):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                original_header = [str(c).strip() for c in next(csv.reader(io.StringIO(blob.decode(encoding))))]
                df = pd.read_csv(io.BytesIO(blob), dtype=str, encoding=encoding, keep_default_na=False, nrows=MAX_ROWS + 1)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise AnalysisError("无法识别CSV编码，请另存为UTF-8。")
    else:
        raise AnalysisError("请上传CSV或XLSX文件。")
    df.columns = [str(x).strip() for x in df.columns]
    if df.empty or len(df) > MAX_ROWS:
        raise AnalysisError(f"文件须包含1至{MAX_ROWS:,}行数据。")
    if len(set(original_header)) != len(original_header):
        raise AnalysisError("存在重复列名，请先修改列名。")
    if any(not column for column in original_header):
        raise AnalysisError("存在空列名，请为每一列填写名称。")
    return df


def infer_mapping(df: pd.DataFrame, mode: str) -> dict:
    return {key: next((a for a in ALIASES[key] if a in df.columns), "") for key in SCHEMAS[mode]}


def _decimal(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value).strip())
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def normalize(df: pd.DataFrame, mode: str, mapping: dict, *, cancellation_prefix: str | None = None, diagnostics: list | None = None) -> tuple[pd.DataFrame, dict]:
    if mode not in SCHEMAS:
        raise AnalysisError("未知分析模板。")
    required = set(SCHEMAS[mode]) - {"entity"}
    if any(not isinstance(v, str) for v in mapping.values()):
        raise AnalysisError("字段映射须为文本列名。")
    if mapping.get("entity") and mapping["entity"] not in df.columns:
        raise AnalysisError("编号映射列不存在。")
    missing = [SCHEMAS[mode][k] for k in required if not mapping.get(k) or mapping[k] not in df.columns]
    if missing:
        raise AnalysisError("需要映射字段：" + "、".join(missing))
    selected = [mapping[k] for k in required | {"entity"} if mapping.get(k)]
    if len(selected) != len(set(selected)):
        raise AnalysisError("同一原始列不能映射到两个不同字段。")
    raw = df.fillna("").copy()
    records, rejected = [], []
    duplicate_count = int(raw.duplicated().sum())
    duplicate_flags = raw.duplicated(keep=False).tolist() if diagnostics is not None else []
    # Duplicate rows are flagged, retained: an invoice may legitimately repeat an item.
    parsed_dates = pd.to_datetime(raw[mapping["date"]], errors="coerce", format="mixed")
    for n, (row, date) in enumerate(zip(raw.to_dict("records"), parsed_dates), 2):
        reason = ""
        if pd.isna(date):
            reason = "日期无效"
        else:
            dimension = str(row[mapping["dimension"]]).strip() or "未填写分类"
            entity = str(row.get(mapping.get("entity", ""), "")).strip()
            if mode == "orders":
                qty, price = _decimal(row[mapping["quantity"]]), _decimal(row[mapping["price"]])
                if qty is None or price is None:
                    reason = "数量或单价无效"
                elif price <= 0 or qty == 0:
                    reason = "零数量或非正单价，隔离等待核查"
                elif qty.copy_abs() > 1_000_000_000 or price > Decimal("1000000000"):
                    reason = "数值超出支持范围"
                else:
                    cents = int((qty * price * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                    if abs(cents) > 9_000_000_000_000:
                        reason = "单行金额超出支持范围"
                    # Cancellation documents must carry a negative quantity; inconsistent signs need review.
                    if cancellation_prefix and entity.upper().startswith(cancellation_prefix.upper()) and qty > 0:
                        reason = "取消单为正数量，口径冲突"
                    values = {"amount": cents, "positive_amount": max(cents, 0), "negative_amount": min(cents, 0), "quantity": str(qty), "unit_price": str(price)}
            else:
                views, clicks = _decimal(row[mapping["views"]]), _decimal(row[mapping["clicks"]])
                if views is None or clicks is None or views < 0 or clicks < 0:
                    reason = "曝光或点击无效"
                elif views != views.to_integral_value() or clicks != clicks.to_integral_value() or clicks > views:
                    reason = "次数须为整数且点击不大于曝光"
                elif views > Decimal("1000000000000"):
                    reason = "曝光次数超出支持范围"
                else:
                    values = {"views": int(views), "clicks": int(clicks)}
            if not reason:
                records.append({"source_row": n, "date": date.strftime("%Y-%m-%d"), "dimension": dimension, "entity": entity, **values})
        if reason:
            rejected.append({"source_row": n, "reason": reason})
        if diagnostics is not None:
            issues = ([('quarantined', reason)] if reason else []) + ([('duplicate_hint', '重复记录提示，未自动删除')] if duplicate_flags[n-2] else [])
            for kind, why in issues:
                diagnostics.append({"source_row": n, "kind": kind, "reason": why,
                    "date_raw": str(row[mapping['date']]), "dimension_raw": str(row[mapping['dimension']]),
                    "entity_raw": str(row.get(mapping.get('entity',''),'')), "original_values": json.dumps(row,ensure_ascii=False,default=str)})
    if not records:
        raise AnalysisError("没有可以分析的有效行，请检查日期、数字和字段映射。")
    frame = pd.DataFrame(records)
    if mode == "orders" and sum(abs(int(x)) for x in frame.amount) > 100_000_000_000_000:
        raise AnalysisError("数据集总金额超出当前精确分析范围，请拆分数据集。")
    quality = {
        "input_rows": len(raw), "valid_rows": len(frame), "rejected_rows": len(rejected),
        "duplicate_rows": duplicate_count, "duplicates_policy": "仅提示，保留原行；没有业务唯一键时不能自动删除。",
        "missing_dimension": int((frame.dimension == "未填写分类").sum()),
        "missing_entity": int((frame.entity == "").sum()),
        "reasons": pd.Series([r["reason"] for r in rejected], dtype=str).value_counts().to_dict(),
        "rejected_examples": rejected[:50], "file_sha256": "",
        "date_start": frame.date.min(), "date_end": frame.date.max(),
        "cancellation_policy": f"数据源明确声明{cancellation_prefix}前缀代表取消单；正数量冲突行隔离" if cancellation_prefix else "未推断订单号前缀含义；按金额正负计算",
    }
    return frame, quality


def metrics(mode: str) -> dict:
    if mode == "orders":
        return {"amount": {"label": "净交易额", "unit": "currency", "definition": "逐行数量×单价，四舍五入到分后求和；包含负数量记录，隔离无效行。"},
                "positive_amount": {"label": "正向交易额", "unit": "currency", "definition": "有效行中正交易金额合计；不是利润或到账金额。"},
                "negative_amount": {"label": "负向调整额", "unit": "currency", "definition": "有效行中负交易金额合计；不能直接等同退款率。"},
                "orders": {"label": "正向订单数", "unit": "count", "definition": "具有正交易额的非空订单编号去重数；不是交易明细行数。"}}
    return {"views": {"label": "曝光次数", "unit": "count", "definition": "有效行曝光次数之和；不是去重用户人数。"},
            "clicks": {"label": "点击次数", "unit": "count", "definition": "有效行点击次数之和；不是去重用户人数。"},
            "ctr": {"label": "点击率", "unit": "percent", "definition": "总点击÷总曝光；不能对各行点击率直接求平均。"}}


def aggregate(frame: pd.DataFrame, mode: str, metric: str) -> float | int | None:
    if metric not in metrics(mode):
        raise AnalysisError("该模板没有此指标，无法根据缺失字段计算利润、ROI或转化率。")
    if metric == "orders":
        if ((frame.amount > 0) & (frame.entity == "")).any():
            raise AnalysisError("部分正向交易记录缺少订单编号，无法可靠统计正向订单数。")
        return int(frame.loc[frame.amount > 0, "entity"].nunique())
    if metric == "ctr":
        views = int(frame.views.sum())
        return round(int(frame.clicks.sum()) / views * 100, 6) if views else None
    value = int(frame[metric].sum())
    return value / 100 if metrics(mode)[metric]["unit"] == "currency" else value


def window(frame: pd.DataFrame, start: str | None, end: str | None) -> pd.DataFrame:
    for date in (start, end):
        if date is not None:
            try:
                if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                    raise ValueError()
                calendar_date.fromisoformat(date)
            except (ValueError, TypeError):
                raise AnalysisError("日期格式必须是有效的YYYY-MM-DD。") from None
    if start and end and start > end:
        raise AnalysisError("开始日期不能晚于结束日期。")
    selected = frame[(frame.date >= (start or frame.date.min())) & (frame.date <= (end or frame.date.max()))]
    if selected.empty:
        raise AnalysisError("所选时间范围没有有效数据。")
    return selected


def run_tool(frame: pd.DataFrame, mode: str, plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise AnalysisError("分析条件必须是对象。")
    for key in ("operation", "metric", "grain"):
        if plan.get(key) is not None and not isinstance(plan[key], str):
            raise AnalysisError("分析操作、指标和粒度须为文本。")
    if "limit" in plan and (not isinstance(plan["limit"], int) or isinstance(plan["limit"], bool)):
        raise AnalysisError("排名数量须为整数。")
    allowed = {"overview", "trend", "ranking", "compare", "quality"}
    operation = plan.get("operation")
    if operation not in allowed:
        raise AnalysisError("仅支持概览、趋势、分类排名、两期比较和数据质量。")
    if operation == "quality":
        return {"operation": operation, "facts": [], "rows": [],"plan":plan,"quality_scope":"full_import_file", "summary": "质量检查针对整个导入文件，含无法归属日期的错误行；请查看质量卡片及完整诊断记录。"}
    metric = plan.get("metric") or ("amount" if mode == "orders" else "clicks")
    definition = metrics(mode).get(metric)
    if not definition:
        raise AnalysisError("请求的指标缺少必要字段，不能计算。")
    selected = window(frame, plan.get("start"), plan.get("end"))
    actual_period = {"start": selected.date.min(), "end": selected.date.max()}
    def coverage(group,requested_start,requested_end):
        lower=requested_start or group.date.min();upper=requested_end or group.date.max()
        return {"requested_start":lower,"requested_end":upper,"actual_start":group.date.min(),"actual_end":group.date.max(),
            "calendar_days":(calendar_date.fromisoformat(upper)-calendar_date.fromisoformat(lower)).days+1,"observed_days":int(group.date.nunique())}
    period_coverage={"current":coverage(selected,plan.get("start"),plan.get("end"))}
    facts, rows, notices = [], [], []
    breakdown = None
    def fact(label, value, **extra):
        f = {"id": f"E{len(facts)+1}", "label": label, "value": value, "unit": definition["unit"], **extra}
        facts.append(f)
        return f
    if operation == "overview":
        fact(definition["label"], aggregate(selected, mode, metric), row_count=len(selected))
    elif operation == "trend":
        grain = plan.get("grain", "month")
        if grain not in {"day", "month"}:
            raise AnalysisError("时间粒度仅支持日或月。")
        grouped = selected.assign(period=selected.date.str[:7] if grain == "month" else selected.date)
        for period, group in grouped.groupby("period", sort=True):
            f = fact(str(period), aggregate(group, mode, metric), row_count=len(group))
            rows.append({"dimension": str(period), "value": f["value"], "evidence_id": f["id"]})
        notices.append("首尾月份可能不完整；趋势图不据此自动给出整月环比结论。")
    elif operation == "ranking":
        values = [(str(d), aggregate(g, mode, metric), len(g)) for d, g in selected.groupby("dimension")]
        values.sort(key=lambda item: (item[1] is not None, item[1] or 0), reverse=True)
        limit = max(1, min(int(plan.get("limit", 10)), 20))
        for dimension, value, count in values[:limit]:
            sample = selected[selected.dimension == dimension]
            extra = {"views": int(sample.views.sum()), "clicks": int(sample.clicks.sum())} if mode == "content" else {}
            f = fact(dimension, value, row_count=count, **extra)
            rows.append({"dimension": dimension, "value": value, "evidence_id": f["id"], "row_count": count, **extra})
        if metric == "ctr":
            notices.append("点击率排名受曝光样本大小影响；不代表内容因果效果或显著性。")
    elif operation == "compare":
        if not all(plan.get(k) for k in ("start", "end", "previous_start", "previous_end")):
            raise AnalysisError("两期比较须指定两个完整日期范围。")
        previous = window(frame, plan["previous_start"], plan["previous_end"])
        period_coverage["previous"]=coverage(previous,plan["previous_start"],plan["previous_end"])
        p_start, p_end = plan["previous_start"], plan["previous_end"]
        if not (p_end < plan["start"] or p_start > plan["end"]):
            raise AnalysisError("比较区间不能重叠。")
        if any(d < frame.date.min() or d > frame.date.max() for d in (p_start, p_end, plan["start"], plan["end"])):
            raise AnalysisError("请求区间超出数据覆盖范围，请选择覆盖范围内的日期。")
        a, b = aggregate(selected, mode, metric), aggregate(previous, mode, metric)
        fact("本期" + definition["label"], a)
        fact("基期" + definition["label"], b)
        delta = round(a - b, 2 if definition["unit"] == "currency" else 6) if a is not None and b is not None else None
        delta_fact = fact("变化量" + ("（百分点）" if metric == "ctr" else ""), delta)
        if metric == "ctr":
            delta_fact["unit"] = "percentage_point"
        if b and a is not None:
            facts.append({"id": "E4", "label": "相对变化", "value": round((a-b)/abs(b)*100, 6), "unit": "percent"})
            if b < 0:
                notices.append("基期为负数，相对变化使用基期绝对值作分母，不能按常规正基期增长率解释。")
        else:
            notices.append("基期为零或不可计算，不能计算相对变化率。")
        days_a = (pd.Timestamp(plan["end"]) - pd.Timestamp(plan["start"])).days + 1
        days_b = (pd.Timestamp(p_end) - pd.Timestamp(p_start)).days + 1
        if days_a != days_b:
            notices.append(f"两期长度不同（{days_a}天与{days_b}天），总量变化同时受天数影响。")
        if metric in {"amount", "positive_amount", "negative_amount", "views", "clicks"}:
            current_totals = selected.groupby("dimension")[metric].sum().to_dict()
            previous_totals = previous.groupby("dimension")[metric].sum().to_dict()
            dimensions = sorted(set(current_totals) | set(previous_totals))
            for dimension in dimensions:
                ac, bc = int(current_totals.get(dimension, 0)), int(previous_totals.get(dimension, 0))
                divisor = 100 if definition["unit"] == "currency" else 1
                rows.append({"dimension": dimension, "current": ac/divisor, "previous": bc/divisor, "value": (ac-bc)/divisor})
            rows.sort(key=lambda x: (-abs(x["value"]), x["dimension"]))
            # Keep the full additive decomposition, not a top-N mislabeled as the total.
            for row in rows:
                f = fact(row["dimension"] + "变化贡献", row["value"], current=row["current"], previous=row["previous"])
                row["evidence_id"] = f["id"]
        else:
            notices.append("比率和去重订单数不具有分类可加性，因此不输出相加式贡献拆解。")
        notices.append("变化贡献是统计拆解，不能证明价格、活动或投放导致变化。")
        if mode == "orders" and metric == "amount":
            breakdown = change_breakdown(previous, selected, rows)
    if any(c["observed_days"]<c["calendar_days"] for c in period_coverage.values()):
        notices.append("所选期间并非每天都有记录；没有记录可能是无业务或未导出，不能单凭表格判断数据完整。")
    return {"operation": operation, "metric": metric, "definition": definition, "period": actual_period,
            "period_coverage":period_coverage, "change_breakdown":breakdown,
            "previous_period": {"start": plan.get("previous_start"), "end": plan.get("previous_end")} if operation == "compare" else None,
            "facts": facts, "rows": rows, "notices": notices, "source_rows": selected.source_row.head(100).astype(int).tolist(),
            "matched_rows": len(selected), "plan": plan}


def evidence_rows(frame: pd.DataFrame, result: dict, evidence_id: str) -> tuple[pd.DataFrame, dict]:
    """Resolve one stored fact back to its exact date/dimension context, including old snapshots."""
    fact = next((f for f in result.get("facts",[]) if f.get("id")==evidence_id),None)
    if fact is None:
        raise AnalysisError("证据编号不存在。")
    plan=result["plan"];operation=result["operation"]
    current=window(frame,plan.get("start"),plan.get("end")).assign(analysis_period="current")
    dimension=None
    if operation=="compare":
        previous=window(frame,plan["previous_start"],plan["previous_end"]).assign(analysis_period="previous")
        contribution=next((r for r in result.get("rows",[]) if r.get("evidence_id")==evidence_id),None)
        if contribution:
            selected=pd.concat([previous,current],ignore_index=True)
            dimension=contribution["dimension"];selected=selected[selected.dimension==dimension]
        elif fact["label"]=="本期"+result["definition"]["label"]:
            selected=current
        elif fact["label"]=="基期"+result["definition"]["label"]:
            selected=previous
        else:
            selected=pd.concat([previous,current],ignore_index=True)
    elif operation=="ranking":
        dimension=fact["label"];selected=current[current.dimension==dimension]
    elif operation=="trend":
        grain=plan.get("grain","month");period=fact["label"]
        selected=current[current.date.str[:7]==period] if grain=="month" else current[current.date==period]
    else:
        selected=current
    totals={}
    for period,group in selected.groupby("analysis_period"):
        if result["metric"]=="orders":
            totals[str(period)]=int(group.loc[group.amount>0,"entity"].nunique())
        elif result["metric"]=="ctr":
            totals[str(period)]={"views":int(group.views.sum()),"clicks":int(group.clicks.sum()),"ctr":aggregate(group,"content","ctr")}
        else:
            totals[str(period)]=aggregate(group,"orders" if result["metric"] in {"amount","positive_amount","negative_amount"} else "content",result["metric"])
    if operation=="compare" and dimension is not None:
        for period in ("previous","current"):
            totals.setdefault(period,0)
    context={"evidence_id":evidence_id,"fact":fact,"definition":result["definition"],"dimension":dimension,"period_totals":totals,
        "matched_rows":len(selected),"record_number_note":"source_row为导入记录序号，表头为1；CSV含换行字段时不等于物理文本行号。"}
    return selected.sort_values(["analysis_period","source_row"]),context


def overview(frame: pd.DataFrame, mode: str) -> list:
    result = []
    for metric, definition in metrics(mode).items():
        try:
            value, error = aggregate(frame, mode, metric), None
        except AnalysisError as exc:
            value, error = None, str(exc)
        result.append({"metric": metric, **definition, "value": value, "error": error})
    return result
