import asyncio
import io
import pandas as pd
from app import ai
from app.exports import csv_response

def test_ambiguous_business_metric_is_clarified_without_model():
    r,trace=asyncio.run(ai.plan_question("GMV与销售额有多少？","orders","2026-08-01","2026-09-28"))
    assert r.get("clarification") and trace["provider"]=="guard"

def test_negative_normalized_quantity_remains_numeric_in_csv():
    raw=csv_response(pd.DataFrame({"quantity":["-1","-HYPERLINK(1)"],"unit_price":["29.50","=1+1"]}),"test.csv").body.decode('utf-8-sig')
    result=pd.read_csv(io.StringIO(raw),dtype=str)
    assert result.quantity.iloc[0]=="-1" and result.quantity.iloc[1].startswith("'")
    assert result.unit_price.iloc[1].startswith("'")

def test_rule_mode_metric_precedence_is_unambiguous():
    assert ai.rule_plan("正向订单数是多少？","orders")["metric"]=="orders"
    assert ai.rule_plan("含负向调整的净交易额是多少？","orders")["metric"]=="amount"

def test_clear_quality_task_uses_verified_tool():
    result,trace=asyncio.run(ai.plan_question("先检查错误日期与重复记录，评估数据质量。","orders","2026-08-01","2026-09-28"))
    assert result["operation"]=="quality" and trace["provider"]=="guard"
