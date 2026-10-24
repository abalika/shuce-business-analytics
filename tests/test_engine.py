import io
import pandas as pd
import pytest
from app.engine import AnalysisError, aggregate, infer_mapping, normalize, read_frame, run_tool


def orders(rows=None):
    # Gold standard: 2×10.01 + 1×10.01 - 1×10.01 + 3×5 = 35.02; two positive invoice IDs.
    raw = pd.DataFrame(rows or [["2026-08-01", "A", "2", "10.01", "O1"], ["2026-08-02", "A", "1", "10.01", "O1"], ["2026-08-03", "A", "-1", "10.01", "C1"], ["2026-09-01", "B", "3", "5", "O2"]], columns=["日期", "商品", "数量", "单价", "订单编号"])
    return normalize(raw, "orders", infer_mapping(raw,"orders"))


def content(rows=None):
    raw=pd.DataFrame(rows or [["2026-08-01","A",100,10,"P1"],["2026-09-01","B",900,9,"P2"]],columns=["日期","内容","曝光次数","点击次数","内容编号"])
    return normalize(raw,"content",infer_mapping(raw,"content"))


def test_gold_money_and_order_distinct():
    f,_=orders()
    assert aggregate(f,"orders","amount")==35.02
    assert aggregate(f,"orders","positive_amount")==45.03
    assert aggregate(f,"orders","negative_amount")==-10.01
    assert aggregate(f,"orders","orders")==2


def test_round_half_up_per_row():
    f,_=orders([["2026-08-01","A",1,"0.005","O1"],["2026-08-02","A",1,"0.005","O2"]])
    assert aggregate(f,"orders","amount")==0.02


def test_duplicate_rows_are_retained():
    row=["2026-08-01","A",1,10,"O1"]
    f,q=orders([row,row])
    assert q["duplicate_rows"]==1 and len(f)==2
    assert aggregate(f,"orders","amount")==20
    assert aggregate(f,"orders","orders")==1


@pytest.mark.parametrize("bad",[["bad date","A",1,10,"O2"],["2026-08-01","A","abc",10,"O2"],["2026-08-01","A",0,10,"O2"],["2026-08-01","A",1,-1,"O2"],["2026-08-01","A","inf",10,"O2"],["2026-08-01","A",1000000000,1000000000,"O2"]])
def test_bad_rows_are_quarantined(bad):
    f,q=orders([["2026-08-01","A",1,10,"O1"],bad])
    assert len(f)==1 and q["rejected_rows"]==1 and q["rejected_examples"][0]["source_row"]==3


def test_missing_order_id_rejects_count_but_keeps_amount():
    f,_=orders([["2026-08-01","A",1,10,""]])
    assert aggregate(f,"orders","amount")==10
    with pytest.raises(AnalysisError):aggregate(f,"orders","orders")

def test_missing_adjustment_id_does_not_block_positive_order_count():
    f,_=orders([["2026-08-01","A",1,10,"O1"],["2026-08-02","A",-1,10,""]])
    assert aggregate(f,"orders","orders")==1 and aggregate(f,"orders","amount")==0


def test_c_prefix_is_source_specific():
    f,_=orders([["2026-08-01","A",1,10,"CLIENT-1"]])
    assert aggregate(f,"orders","amount")==10
    raw=pd.DataFrame([["2026-08-01","A",1,10,"C123"],["2026-08-01","A",1,10,"123"]],columns=["InvoiceDate","Description","Quantity","UnitPrice","InvoiceNo"])
    f,q=normalize(raw,"orders",infer_mapping(raw,"orders"),cancellation_prefix="C")
    assert len(f)==1 and q["rejected_rows"]==1
    ordinary,q=normalize(raw,"orders",infer_mapping(raw,"orders"))
    assert len(ordinary)==2 and q["rejected_rows"]==0


def test_weighted_rate_instead_of_mean():
    f,_=content()
    assert aggregate(f,"content","ctr")==1.9 # (10+9)/(100+900), not (10%+1%)/2.


def test_zero_denominator_is_not_zero_rate():
    f,_=content([["2026-08-01","A",0,0,"P1"]])
    assert aggregate(f,"content","ctr") is None


def test_clicks_cannot_exceed_views():
    f,q=content([["2026-08-01","A",100,10,"P1"],["2026-08-02","B",10,20,"P2"]])
    assert len(f)==1 and q["rejected_rows"]==1


def test_compare_contribution_reconciles():
    f,_=orders()
    r=run_tool(f,"orders",{"operation":"compare","metric":"amount","previous_start":"2026-08-01","previous_end":"2026-08-03","start":"2026-09-01","end":"2026-09-01"})
    assert r["facts"][0]["value"]==15
    assert r["facts"][1]["value"]==20.02
    assert r["facts"][2]["value"]==-5.02
    assert round(sum(x["value"] for x in r["rows"]),2)==-5.02
    assert any("长度不同" in n for n in r["notices"])
    assert any("不能证明" in n for n in r["notices"])


@pytest.mark.parametrize("metric,mode",[("ctr","content"),("orders","orders")])
def test_nonadditive_metrics_do_not_get_false_contributions(metric,mode):
    f,_=content() if mode=="content" else orders()
    r=run_tool(f,mode,{"operation":"compare","metric":metric,"previous_start":"2026-08-01","previous_end":"2026-08-01","start":"2026-09-01","end":"2026-09-01"})
    assert r["rows"]==[]


@pytest.mark.parametrize("plan",[{"operation":"delete"},{"operation":["overview"]},{"operation":"overview","metric":"profit"},{"operation":"ranking","limit":"bad"},{"operation":"trend","grain":"year"},{"operation":"overview","start":"bad"},{"operation":"compare","start":"2026-08-01","end":"2026-08-02"},{"operation":"overview","start":"2030-01-01"}])
def test_invalid_queries_fail_closed(plan):
    f,_=orders()
    with pytest.raises(AnalysisError):run_tool(f,"orders",plan)


def test_overlapping_periods_rejected():
    f,_=orders()
    with pytest.raises(AnalysisError):run_tool(f,"orders",{"operation":"compare","metric":"amount","previous_start":"2026-08-01","previous_end":"2026-08-03","start":"2026-08-02","end":"2026-09-01"})


def test_incomplete_coverage_rejected_for_comparison():
    f,_=orders()
    with pytest.raises(AnalysisError):run_tool(f,"orders",{"operation":"compare","metric":"amount","previous_start":"2026-08-01","previous_end":"2026-08-31","start":"2026-09-01","end":"2026-09-30"})


def test_csv_and_xlsx_mapping():
    csv="日期,商品,数量,单价,订单编号\n2026-08-01,A,2,10,O1\n".encode("utf-8-sig")
    raw=read_frame(csv,"example.csv")
    assert infer_mapping(raw,"orders")["date"]=="日期"
    out=io.BytesIO();raw.to_excel(out,index=False)
    f,_=normalize(read_frame(out.getvalue(),"example.xlsx"),"orders",infer_mapping(raw,"orders"))
    assert aggregate(f,"orders","amount")==20


def test_duplicate_headers_and_mapping_rejected():
    with pytest.raises(AnalysisError):read_frame(b"date,date\n2026-01-01,2026-01-01","bad.csv")
    raw=pd.DataFrame([["2026-01-01",1,1]],columns=["日期","商品","单价"])
    with pytest.raises(AnalysisError):normalize(raw,"orders",{"date":"日期","dimension":"商品","quantity":"商品","price":"单价"})
