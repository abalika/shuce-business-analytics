from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import logging
import os
import sqlite3
import time
import uuid
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import lru_cache
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .engine import AnalysisError, SCHEMAS, infer_mapping, metrics, normalize, overview, read_frame, run_tool, evidence_rows
from .exports import csv_response
from .integrity import IntegrityError, fingerprints, verify_artifacts
from .ai import plan_question, status
from .brief import generate_brief
from .scenarios import SCENARIOS, guide_for
from .breakdown import report_breakdown

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("SHUCE_DATA_DIR", str(ROOT / "data")))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "index.sqlite3"


def connect():
    con = sqlite3.connect(DB, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def initialize():
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS analyses (id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)")
        con.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL)")


initialize()
from .version import VERSION

app = FastAPI(title="数策 · 可复核业务分析", version=VERSION)


@app.middleware("http")
async def local_write_guard(request: Request, call_next):
    if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        origin = request.headers.get("origin")
        local_port = os.getenv('SHUCE_PORT', '8765')
        if origin and origin not in {f"http://127.0.0.1:{local_port}", f"http://localhost:{local_port}"}:
            return Response("Origin rejected", status_code=403)
        if request.headers.get("x-shuce-client") != "1":
            return Response("Missing client header", status_code=403)
    try:
        return await call_next(request)
    except Exception as exc:
        # Do not log exception messages, request bodies, paths or provider credentials.
        incident = uuid.uuid4().hex[:12]
        logging.getLogger('shuce').error('request_failed incident=%s category=%s', incident, type(exc).__name__)
        return JSONResponse({'code': 'unexpected_error', 'incident': incident,
                             'detail': '请求暂未完成。请保留草稿，先查看历史确认是否已保存，再重试；若持续失败，请提供事件编号 ' + incident + '，并查看 work/server-端口.log。'},
                            status_code=503)


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc), "code": "data_integrity_failed"}, status_code=409)


@app.exception_handler(AnalysisError)
async def analysis_error(request: Request, exc: AnalysisError):
    from fastapi.responses import JSONResponse
    body = {"detail": str(exc), "code": "invalid_analysis"}
    if getattr(exc, 'trace', None):
        body['trace'] = exc.trace
    return JSONResponse(body, status_code=422)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Pydantic's default response includes the supplied input. Do not echo source
    # records, free text or accidentally pasted credentials back into error logs.
    return JSONResponse({"detail": "输入格式不正确，请检查必填字段、类型、长度和分页范围。",
                         "code": "invalid_request",
                         "issues": [{"type": item['type'], "field": '.'.join(str(x) for x in item['loc'][:4])}
                                    for item in exc.errors()]}, status_code=422)


@app.exception_handler(sqlite3.Error)
async def database_error(request: Request, exc: sqlite3.Error):
    return JSONResponse({"detail": "数据库暂不可用，请关闭重复运行实例、检查磁盘与权限后重试。若持续失败，先保留原目录，再核查或恢复已校验备份；不要反复提交。",
                         "code": "database_unavailable"}, status_code=503)


@app.exception_handler(OSError)
async def storage_error(request: Request, exc: OSError):
    return JSONResponse({"detail": "本地文件读写失败，请检查磁盘空间、目录权限及文件占用。保留页面草稿，核查历史记录后再重试；未收到成功响应不能视为保存成功。",
                         "code": "storage_unavailable"}, status_code=503)


def get_dataset(dataset_id):
    with connect() as con:
        row = con.execute("SELECT metadata FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if not row:
        raise HTTPException(404, "未找到数据集。")
    return json.loads(row["metadata"])


@lru_cache(maxsize=3)
def _cached_frame(dataset_id, normalized_hash):
    return pd.read_csv(DATA / f"{dataset_id}.csv.gz", dtype={"date": str, "dimension": str, "entity": str, "quantity":str, "unit_price":str}, keep_default_na=False)


def load_frame(dataset_id):
    meta = get_dataset(dataset_id)
    hashes = verify_artifacts(DATA, meta)
    return _cached_frame(dataset_id, hashes['normalized'])


load_frame.cache_clear = _cached_frame.cache_clear


def store_dataset(blob, filename, mode, mapping, name, source, currency, *, cancellation_prefix=None, scenario_key=None):
    t0 = time.perf_counter()
    try:
        raw = read_frame(blob, filename)
    except AnalysisError:
        raise
    except Exception:
        raise AnalysisError("文件无法解析，请检查格式、内容和编码。") from None
    diagnostics=[]
    frame, quality = normalize(raw, mode, mapping or infer_mapping(raw, mode), cancellation_prefix=cancellation_prefix,diagnostics=diagnostics)
    dataset_id = uuid.uuid4().hex
    quality["file_sha256"] = hashlib.sha256(blob).hexdigest()
    frame.to_csv(DATA / f"{dataset_id}.csv.gz", index=False, compression="gzip")
    pd.DataFrame(diagnostics,columns=["source_row","kind","reason","date_raw","dimension_raw","entity_raw","original_values"]).to_csv(DATA/f"{dataset_id}-diagnostics.csv.gz",index=False,compression="gzip")
    # Original bytes remain local, with generated names; client paths never become filesystem paths.
    (DATA / f"{dataset_id}.raw").write_bytes(blob)
    meta = {"id": dataset_id, "name": name or Path(filename).name, "filename": Path(filename).name,
            "mode": mode, "source": source or "用户导入；业务真实性未由系统验证", "currency": currency,
            "quality": quality, "mapping": mapping or infer_mapping(raw, mode), "created_at": datetime.now(timezone.utc).isoformat(),
            "import_ms": round((time.perf_counter()-t0)*1000), "metrics": metrics(mode),"schema_version":2,"cancellation_prefix":cancellation_prefix}
    meta['artifact_sha256'] = fingerprints(DATA, meta)
    if scenario_key:
        meta["guide"] = guide_for(scenario_key)
    with connect() as con:
        con.execute("INSERT INTO datasets VALUES (?,?)", (dataset_id, json.dumps(meta, ensure_ascii=False)))
    return meta


@app.get("/api/status")
def api_status():
    return {"app_id": "shuce", "app": "数策", "version": VERSION, "ai": status(), "schemas": SCHEMAS,
            "instance_id": os.getenv('SHUCE_INSTANCE_ID'), "pid": os.getpid()}


@app.get("/api/datasets")
def datasets():
    with connect() as con:
        return [json.loads(row["metadata"]) for row in con.execute("SELECT metadata FROM datasets ORDER BY rowid DESC")]


async def upload_bytes(file):
    blob = await file.read(100 * 1024 * 1024 + 1)
    if len(blob) > 100 * 1024 * 1024:
        raise AnalysisError("文件不能超过100MB。")
    if (file.filename or "").lower().endswith(".xlsx"):
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                if sum(i.file_size for i in z.infolist()) > 250 * 1024 * 1024:
                    raise AnalysisError("Excel解压后过大，请改用CSV。")
        except zipfile.BadZipFile:
            raise AnalysisError("XLSX文件损坏。") from None
    return blob


@app.post("/api/preview")
async def preview(file: UploadFile = File(...), mode: str = Form("orders")):
    if mode not in SCHEMAS:
        raise AnalysisError("未知模板。")
    blob = await upload_bytes(file)
    try:
        frame = read_frame(blob, file.filename or "")
    except AnalysisError:
        raise
    except Exception:
        raise AnalysisError("无法读取文件，请检查文件内容。") from None
    return {"columns": list(frame.columns), "mapping": infer_mapping(frame, mode), "rows": len(frame), "sample": frame.fillna("").head(5).to_dict("records")}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...), mode: str = Form("orders"), mapping: str = Form("{}"), name: str = Form(""), source: str = Form(""), currency: str = Form("元")):
    if mode not in SCHEMAS or currency not in {"元", "GBP", "USD", "金额单位"}:
        raise AnalysisError("模板或金额单位无效。")
    try:
        mapped = json.loads(mapping)
        if not isinstance(mapped, dict):
            raise ValueError()
    except ValueError:
        raise AnalysisError("字段映射必须是JSON对象。") from None
    return store_dataset(await upload_bytes(file), file.filename or "", mode, mapped, name[:120], source[:600], currency)


@app.post("/api/demo/{mode}")
def demo(mode: str):
    if mode not in {"orders", "content"}:
        raise AnalysisError("未知模板。")
    path = ROOT / "examples" / f"{mode}.csv"
    return store_dataset(path.read_bytes(), path.name, mode, {}, "订单演示" if mode == "orders" else "内容表现演示", "明确标注的人工构造测试数据；不是真实商家/账号业绩", "元")


@app.post("/api/scenarios/{key}")
def import_scenario(key: str):
    if key not in SCENARIOS:
        raise AnalysisError("未知演示场景。")
    s = SCENARIOS[key]
    blob = (ROOT / "examples" / s["filename"]).read_bytes()
    # Reuse exact built-in source only, never silently replace an imported file or old snapshot.
    digest = hashlib.sha256(blob).hexdigest()
    for meta in datasets():
        if meta.get("guide", {}).get("id") == key and meta["quality"]["file_sha256"] == digest:
            if meta["guide"] != guide_for(key):
                meta["guide"] = guide_for(key)
                with connect() as con:
                    con.execute("UPDATE datasets SET metadata=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), meta["id"]))
            return meta
    return store_dataset(blob, s["filename"], s["mode"], {}, s["name"], s["source"], "元", scenario_key=key)


@app.get("/api/datasets/{dataset_id}")
def dataset_detail(dataset_id: str):
    meta = get_dataset(dataset_id)
    frame = load_frame(dataset_id)
    default = "amount" if meta["mode"] == "orders" else "clicks"
    sample = frame.drop_duplicates(subset=["dimension"]).head(3)
    if meta["mode"] == "orders":
        sample = pd.concat([sample, frame[frame["negative_amount"] < 0].head(1)]).drop_duplicates(subset=["source_row"])
    return {"dataset": meta, "kpis": overview(frame, meta["mode"]), "sample_rows": sample.to_dict("records"), "trend": run_tool(frame, meta["mode"], {"operation": "trend", "metric": default, "grain": "day" if meta.get("guide") else "month"}), "ranking": run_tool(frame, meta["mode"], {"operation": "ranking", "metric": default, "limit": 6})}


@app.get("/api/datasets/{dataset_id}/original")
def original_file(dataset_id: str):
    meta = get_dataset(dataset_id)
    verify_artifacts(DATA, meta)
    suffix = ".xlsx" if meta["filename"].lower().endswith(".xlsx") else ".csv"
    return FileResponse(DATA / f"{dataset_id}.raw", filename="original"+suffix, media_type="application/octet-stream")


def load_diagnostics(dataset_id):
    verify_artifacts(DATA, get_dataset(dataset_id))
    path=DATA/f"{dataset_id}-diagnostics.csv.gz"
    if not path.exists():
        raise AnalysisError("历史数据集尚未升级完整诊断记录。")
    return pd.read_csv(path,keep_default_na=False)


@app.get("/api/datasets/{dataset_id}/diagnostics")
def diagnostics_page(dataset_id:str,offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    frame=load_diagnostics(dataset_id)
    return {"total":len(frame),"offset":offset,"limit":limit,"rows":frame.iloc[offset:offset+limit].to_dict("records"),"note":"诊断表一行一项问题，同一记录可能有多个提示；重复提示不是删除建议。"}


@app.get("/api/datasets/{dataset_id}/diagnostics/export")
def export_diagnostics(dataset_id:str):
    return csv_response(load_diagnostics(dataset_id),"quality-diagnostics.csv")


@app.get("/api/datasets/{dataset_id}/reports")
def dataset_reports(dataset_id:str):
    get_dataset(dataset_id)
    with connect() as con:
        rows=con.execute("SELECT r.id,r.analysis_id,r.title,r.created_at FROM reports r JOIN analyses a ON a.id=r.analysis_id WHERE a.dataset_id=? ORDER BY r.rowid DESC LIMIT 100",(dataset_id,)).fetchall()
    return [dict(row) for row in rows]


class AnalyzeBody(BaseModel):
    dataset_id: str
    plan: dict | None = None
    question: str | None = Field(default=None, max_length=1500)


@app.post("/api/analyze")
async def analyze(body: AnalyzeBody):
    meta = get_dataset(body.dataset_id)
    trace = {"provider": "structured", "label": "结构化条件；未调用模型", "tokens": None, "elapsed_ms": 0}
    if body.question:
        plan, trace = await plan_question(body.question, meta["mode"], meta["quality"]["date_start"], meta["quality"]["date_end"])
    elif body.plan:
        plan = body.plan
    else:
        raise AnalysisError("请填写问题或选择分析条件。")
    if plan.get("clarification") or plan.get("unsupported"):
        return {"clarification": str(plan.get("clarification", "")), "unsupported": str(plan.get("unsupported", "")), "trace": trace}
    t0 = time.perf_counter()
    result = run_tool(load_frame(body.dataset_id), meta["mode"], plan)
    if result["operation"] == "quality":
        result["quality"] = meta["quality"]
    result.update(id=uuid.uuid4().hex, dataset_id=body.dataset_id, question=body.question or "结构化分析", trace=trace,
                  dataset_name=meta["name"], currency=meta["currency"], source=meta["source"], file_sha256=meta["quality"]["file_sha256"],
                  compute_ms=round((time.perf_counter()-t0)*1000), created_at=datetime.now(timezone.utc).isoformat())
    if trace["provider"] == "model" and result.get("facts"):
        result["brief"] = await generate_brief(result)
    with connect() as con:
        con.execute("INSERT INTO analyses VALUES (?,?,?,?)", (result["id"], body.dataset_id, json.dumps(result, ensure_ascii=False), result["created_at"]))
    return result


@app.get("/api/history/{dataset_id}")
def history(dataset_id: str):
    get_dataset(dataset_id)
    with connect() as con:
        return [json.loads(r["result"]) for r in con.execute("SELECT result FROM analyses WHERE dataset_id=? ORDER BY rowid DESC LIMIT 20", (dataset_id,))]


def get_analysis(analysis_id):
    with connect() as con:
        row = con.execute("SELECT result FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    if not row:
        raise HTTPException(404, "分析记录不存在。")
    return json.loads(row["result"])


@app.post("/api/analyses/{analysis_id}/brief")
async def analysis_brief(analysis_id: str):
    result = get_analysis(analysis_id)
    brief = await generate_brief(result)
    # Append a new immutable analysis version so previous reports keep their original evidence.
    result.update(id=uuid.uuid4().hex, parent_analysis_id=analysis_id, brief=brief, created_at=datetime.now(timezone.utc).isoformat())
    with connect() as con:
        con.execute("INSERT INTO analyses VALUES (?,?,?,?)", (result["id"], result["dataset_id"], json.dumps(result, ensure_ascii=False), result["created_at"]))
    return result


@app.get("/api/analyses/{analysis_id}/export")
def export_analysis(analysis_id: str):
    result = get_analysis(analysis_id)
    return Response(json.dumps(result, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="analysis-{analysis_id[:8]}.json"'})


@app.get("/api/analyses/{analysis_id}/evidence/{evidence_id}")
def evidence_page(analysis_id:str,evidence_id:str,offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    result=get_analysis(analysis_id)
    frame,context=evidence_rows(load_frame(result["dataset_id"]),result,evidence_id)
    return {**context,"analysis_id":analysis_id,"currency":result["currency"],"offset":offset,"limit":limit,"rows":frame.iloc[offset:offset+limit].to_dict("records"),"source":result["source"],"file_sha256":result["file_sha256"]}


@app.get("/api/analyses/{analysis_id}/evidence/{evidence_id}/export")
def evidence_export(analysis_id:str,evidence_id:str):
    result=get_analysis(analysis_id)
    frame,_=evidence_rows(load_frame(result["dataset_id"]),result,evidence_id)
    return csv_response(frame,"evidence-"+evidence_id+".csv")


@app.get("/api/analyses/{analysis_id}/rows")
def export_rows(analysis_id: str):
    result = get_analysis(analysis_id)
    if result["operation"]=="quality":
        return csv_response(load_diagnostics(result["dataset_id"]),"quality-diagnostics.csv")
    frame = load_frame(result["dataset_id"])
    plan = result["plan"]
    from .engine import window
    selected = window(frame, plan.get("start"), plan.get("end")) if result["operation"] != "quality" else frame
    selected = selected.assign(analysis_period="current")
    if result["operation"] == "compare":
        previous = window(frame, plan["previous_start"], plan["previous_end"]).assign(analysis_period="previous")
        selected = pd.concat([previous, selected], ignore_index=True)
    return csv_response(selected,"evidence-rows.csv")


class ReportBody(BaseModel):
    analysis_id: str
    title: str = Field(default="业务数据分析报告", min_length=1, max_length=120)
    note: str = Field(default="", max_length=5000)


@app.post("/api/reports")
def create_report(body: ReportBody):
    get_analysis(body.analysis_id)
    report_id = uuid.uuid4().hex
    with connect() as con:
        con.execute("INSERT INTO reports VALUES (?,?,?,?,?)", (report_id, body.analysis_id, body.title, body.note, datetime.now(timezone.utc).isoformat()))
    return {"id": report_id, "url": f"/reports/{report_id}"}


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def report_page(report_id: str):
    with connect() as con:
        record = con.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if not record:
        raise HTTPException(404, "报告不存在。")
    result = get_analysis(record["analysis_id"])
    local_created=datetime.fromisoformat(record["created_at"]).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S（Asia/Shanghai）")
    esc = html.escape
    facts = "".join(f"<tr><td>{esc(f['id'])}</td><td>{esc(f['label'])}</td><td>{esc(str(f['value'] if f['value'] is not None else '不可计算'))}</td><td>{esc(result['currency'] if f['unit']=='currency' else '%' if f['unit']=='percent' else '个百分点' if f['unit']=='percentage_point' else '次/个')}</td></tr>" for f in result.get("facts", [])[:24])
    if len(result.get("facts", [])) > 24:
        facts += '<tr><td colspan="4">报告显示前24项证据，完整分类贡献保留在分析JSON中。</td></tr>'
    notices = "".join(f"<li>{esc(n)}</li>" for n in result.get("notices", []))
    quality = f"<pre>{esc(json.dumps(result.get('quality'), ensure_ascii=False, indent=2))}</pre>" if result.get("quality") else ""
    breakdown_html = report_breakdown(result)
    coverage_html = ""
    if result.get("period_coverage"):
        coverage_html = "<h2>期间与记录覆盖</h2>" + "".join(f"<p>{'基期' if period == 'previous' else '本期'}：请求 {esc(c['requested_start'])} 至 {esc(c['requested_end'])}（{c['calendar_days']}天）；有记录 {c['observed_days']}天，实际记录 {esc(c['actual_start'])} 至 {esc(c['actual_end'])}。</p>" for period, c in result["period_coverage"].items())
    brief = result.get("brief", {})
    brief_html = ""
    if brief.get("status") == "validated":
        for key, title in (("facts", "已计算事实"), ("hypotheses", "AI待核查解释"), ("next_checks", "AI建议的核查步骤")):
            brief_html += f"<h2>{title}</h2><ul>" + "".join(f"<li>{esc(x['text'])} <small>[{esc(', '.join(x['evidence_ids']))}]</small></li>" for x in brief[key]) + "</ul>"
        brief_html += f"<p class='small'>{esc(brief['validation'])}</p>"
    elif brief:
        brief_html = f"<p>AI摘要状态：{esc(brief.get('message', '未生成'))}</p>"
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(record['title'])}</title><style>body{{font:16px/1.7 system-ui;max-width:900px;margin:48px auto;padding:24px;color:#163833;overflow-wrap:anywhere}}h1{{font-size:32px}}table{{width:100%;display:block;overflow-x:auto;border-collapse:collapse}}td,th{{border-bottom:1px solid #ddd;padding:10px;text-align:left}}.small{{color:#687772;font-size:13px;overflow-wrap:anywhere}}pre{{white-space:pre-wrap}}button{{padding:10px 18px;background:#106956;color:white;border:0;border-radius:8px}}@media print{{table{{display:table;overflow:visible}}button{{display:none}}body{{margin:0;padding:0}}tr{{break-inside:avoid}}}}</style><button onclick="window.print()">打印 / 保存PDF</button><p>数策 · 分析快照</p><h1>{esc(record['title'])}</h1><p>数据集：{esc(result['dataset_name'])}</p><p>来源：{esc(result['source'])}</p><p>问题：{esc(result['question'])}</p><p>口径：{esc(result.get('definition',{}).get('definition','数据质量检查（整个导入文件）'))}</p><p>查询条件：{esc(json.dumps(result.get('plan'),ensure_ascii=False))}</p><table><tr><th>证据</th><th>指标/分类</th><th>结果</th><th>单位</th></tr>{facts}</table>{quality}{coverage_html}{breakdown_html}{brief_html}<h2>解释边界</h2><ul>{notices}</ul><h2>复盘说明与后续核查</h2><pre>{esc(record['note']) or '尚未填写。'}</pre><p class="small">记录时间：{esc(local_created)}<br>文件SHA-256：{esc(result['file_sha256'])}<br>查询方式：{esc(result['trace']['label'])}；计算来自确定性工具。公开历史数据与人工模拟数据均不能作为本人商业业绩。</p><p><a href="/api/analyses/{result['id']}/export">下载完整分析记录JSON</a> · <a href="/api/analyses/{result['id']}/rows">下载证据记录CSV</a></p></html>'''


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "ops.html")


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.get("/explore")
def legacy_explore():
    return FileResponse(ROOT / "web" / "index.html")

from .reviews import router as review_router
app.include_router(review_router)
