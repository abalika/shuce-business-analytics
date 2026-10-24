import io
import json
import pandas as pd
import pytest
from app.engine import evidence_rows,normalize,infer_mapping,run_tool,AnalysisError
from tests.test_api import client,seed

def test_compare_dimension_evidence_reconciles_both_periods(client):
    d=seed(client)
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"compare","metric":"amount","previous_start":"2026-08-01","previous_end":"2026-08-28","start":"2026-09-01","end":"2026-09-28"}}).json()
    row=next(x for x in r["rows"] if x["dimension"]=="帆布包")
    detail=client.get(f"/api/analyses/{r['id']}/evidence/{row['evidence_id']}?limit=1").json()
    assert detail["dimension"]=="帆布包" and len(detail["rows"])==1 and detail["matched_rows"]>1
    assert round(detail["period_totals"]["current"]-detail["period_totals"]["previous"],2)==row["value"]
    assert "quantity" in detail["rows"][0] and "unit_price" in detail["rows"][0]
    csv=client.get(f"/api/analyses/{r['id']}/evidence/{row['evidence_id']}/export").text
    assert "quantity" in csv and "帆布包" in csv and "桌面摆件" not in csv

def test_global_evidence_does_not_restrict_to_one_dimension(client):
    d=seed(client)
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"ranking","metric":"amount"}}).json()
    f=r["facts"][0]
    detail=client.get(f"/api/analyses/{r['id']}/evidence/{f['id']}").json()
    assert all(x["dimension"]==f["label"] for x in detail["rows"])
    assert detail["period_totals"]["current"]==f["value"]
    assert client.get(f"/api/analyses/{r['id']}/evidence/E999").status_code==422
    assert client.get(f"/api/analyses/{r['id']}/evidence/{f['id']}?offset=-1").status_code==422

def test_monthly_evidence_is_filtered_to_month(client):
    d=seed(client)
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"trend","metric":"amount"}}).json()
    detail=client.get(f"/api/analyses/{r['id']}/evidence/E1").json()
    assert all(x["date"].startswith("2026-08") for x in detail["rows"])
    assert detail["period_totals"]["current"]==r["facts"][0]["value"]

def test_quality_export_is_complete_and_duplicates_are_hints(client):
    rows=[f"bad-{i},A,1,10,O{i}" for i in range(70)]
    rows+= ["2026-08-01,A,1,10,O1"]*2
    blob=("日期,商品,数量,单价,订单编号\n"+"\n".join(rows)).encode()
    d=client.post("/api/import",files={"file":("test.csv",blob)}).json()
    assert len(d["quality"]["rejected_examples"])==50 and d["quality"]["rejected_rows"]==70
    page=client.get(f"/api/datasets/{d['id']}/diagnostics?offset=70&limit=10").json()
    assert page["total"]==72 and len(page["rows"])==2 and all(x["kind"]=="duplicate_hint" for x in page["rows"])
    exported=client.get(f"/api/datasets/{d['id']}/diagnostics/export")
    frame=pd.read_csv(io.StringIO(exported.text))
    assert len(frame)==72

def test_saved_reports_are_scoped_and_reopenable(client):
    d=seed(client);other=seed(client,"content")
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"overview","metric":"amount"}}).json()
    report=client.post("/api/reports",json={"analysis_id":r["id"],"title":"周期复盘"}).json()
    listed=client.get(f"/api/datasets/{d['id']}/reports").json()
    assert len(listed)==1 and listed[0]["id"]==report["id"]
    assert client.get(f"/api/datasets/{other['id']}/reports").json()==[]

def test_coverage_distinguishes_record_days_from_calendar_days(client):
    d=seed(client,"content")
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"overview","metric":"clicks","start":"2026-08-01","end":"2026-08-31"}}).json()
    c=r["period_coverage"]["current"]
    assert c["calendar_days"]==31 and c["observed_days"]==3 and c["actual_end"]=="2026-08-03"

def test_dimension_starting_with_period_label_not_misclassified():
    raw=pd.DataFrame([["2026-08-01","本期活动",1,10,"O1"],["2026-09-01","另一个",1,20,"O2"]],columns=["日期","商品","数量","单价","订单编号"])
    frame,_=normalize(raw,"orders",infer_mapping(raw,"orders"))
    r=run_tool(frame,"orders",{"operation":"compare","metric":"amount","previous_start":"2026-08-01","previous_end":"2026-08-01","start":"2026-09-01","end":"2026-09-01"})
    row=next(x for x in r["rows"] if x["dimension"]=="本期活动")
    selected,context=evidence_rows(frame,r,row["evidence_id"])
    assert set(selected.dimension)=={"本期活动"} and context["period_totals"]=={"previous":10,"current":0}

def test_quality_snapshot_has_plan_and_exports_diagnostics(client):
    d=seed(client)
    r=client.post("/api/analyze",json={"dataset_id":d["id"],"plan":{"operation":"quality"}}).json()
    assert r["plan"]=={"operation":"quality"} and r["quality_scope"]=="full_import_file"
    csv=client.get(f"/api/analyses/{r['id']}/rows").text
    assert "quarantined" in csv and "duplicate_hint" in csv
