"""Immutable operational review sources/snapshots alongside legacy analysis tables."""
import hashlib,html,json,uuid
from datetime import datetime,timezone
from pathlib import Path
import pandas as pd
from fastapi import APIRouter,File,Form,HTTPException,UploadFile,Query
from fastapi.responses import FileResponse,Response,HTMLResponse
from pydantic import BaseModel,Field
from .engine import AnalysisError,read_frame
from .review_engine import SCHEMAS,mapping_for,normalize_source,build_review
from .review_demo import demo_blobs,demo_task
from .exports import csv_response
from .brief import generate_brief
from .review_export import review_workbook
from .integrity import fingerprints, verify_artifacts

router=APIRouter(prefix='/api/ops')


def host():
    from . import main
    return main


def initialize():
    with host().connect() as con:
        con.execute('CREATE TABLE IF NOT EXISTS review_sources(id TEXT PRIMARY KEY,metadata TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,result TEXT NOT NULL,created_at TEXT NOT NULL)')


def sources_list():
    initialize()
    with host().connect() as con:return [json.loads(r['metadata']) for r in con.execute('SELECT metadata FROM review_sources ORDER BY rowid DESC')]


def get_source(source_id):
    initialize()
    with host().connect() as con:r=con.execute('SELECT metadata FROM review_sources WHERE id=?',(source_id,)).fetchone()
    if not r:raise HTTPException(404,'复盘数据源不存在。')
    return json.loads(r['metadata'])


def load_source(source_id):
    meta=get_source(source_id)
    verify_artifacts(host().DATA,meta,True)
    types={k:str for k in SCHEMAS[meta['kind']] if k not in {'open_minutes','unavailable_minutes'}}
    return pd.read_csv(host().DATA/f'{source_id}.review.csv.gz',dtype=types,keep_default_na=False)


def store_source(blob,filename,kind,mapping,name,source,currency):
    if kind not in SCHEMAS or currency not in {'元','GBP','USD','金额单位'}:raise AnalysisError('数据类型或单位不支持。')
    try:raw=read_frame(blob,filename)
    except AnalysisError:raise
    except Exception:raise AnalysisError('文件无法解析，请检查内容和编码。') from None
    mapping=mapping or mapping_for(raw,kind)
    frame,quality,diagnostics=normalize_source(raw,kind,mapping)
    digest=hashlib.sha256(blob).hexdigest();source_id=uuid.uuid4().hex
    frame.to_csv(host().DATA/f'{source_id}.review.csv.gz',index=False,compression='gzip')
    pd.DataFrame(diagnostics,columns=['source_row','kind','reason','original_values']).to_csv(host().DATA/f'{source_id}.review-diagnostics.csv.gz',index=False,compression='gzip')
    (host().DATA/f'{source_id}.review.raw').write_bytes(blob)
    meta={'id':source_id,'name':name[:120] or Path(filename).name,'filename':Path(filename).name,'kind':kind,'source':source[:600] or '用户导入，真实性未验证','currency':currency,'mapping':mapping,'quality':quality,'file_sha256':digest,'created_at':datetime.now(timezone.utc).isoformat(),'contract_version':'retail-review-2.0'}
    meta['artifact_sha256']=fingerprints(host().DATA,meta,True)
    initialize()
    with host().connect() as con:con.execute('INSERT INTO review_sources VALUES (?,?)',(source_id,json.dumps(meta,ensure_ascii=False)))
    return meta


@router.get('/schemas')
def schemas():return SCHEMAS


@router.get('/sources')
def list_sources():return sources_list()


@router.post('/preview')
async def preview(file:UploadFile=File(...),kind:str=Form('sales')):
    if kind not in SCHEMAS:raise AnalysisError('未知复盘数据类型。')
    try:raw=read_frame(await host().upload_bytes(file),file.filename or '')
    except AnalysisError:raise
    except Exception:raise AnalysisError('无法读取该文件。') from None
    return {'columns':list(raw.columns),'mapping':mapping_for(raw,kind),'sample':raw.fillna('').head(4).to_dict('records'),'rows':len(raw)}


@router.post('/sources')
async def import_source(file:UploadFile=File(...),kind:str=Form('sales'),mapping:str=Form('{}'),name:str=Form(''),source:str=Form(''),currency:str=Form('元')):
    try:mapped=json.loads(mapping)
    except ValueError:raise AnalysisError('字段映射须为JSON对象。') from None
    if not isinstance(mapped,dict):raise AnalysisError('字段映射须为JSON对象。')
    return store_source(await host().upload_bytes(file),file.filename or '',kind,mapped,name,source,currency)


@router.get('/sources/{source_id}/original')
def original(source_id:str):
    meta=get_source(source_id)
    verify_artifacts(host().DATA,meta,True)
    return FileResponse(host().DATA/f'{source_id}.review.raw',filename=meta['filename'])


@router.get('/sources/{source_id}/diagnostics')
def diagnostic_export(source_id:str):
    verify_artifacts(host().DATA,get_source(source_id),True)
    return csv_response(pd.read_csv(host().DATA/f'{source_id}.review-diagnostics.csv.gz',keep_default_na=False),'review-diagnostics.csv')


class ReviewBody(BaseModel):
    sales_id:str
    targets_id:str
    operations_id:str
    title:str=Field(min_length=1,max_length=120)
    question:str=Field(min_length=1,max_length=1500)
    start:str
    end:str
    previous_start:str|None=None
    previous_end:str|None=None
    metric:str='amount'
    confirmed_complete:bool=False
    confirmation_note:str=Field(default='',max_length=600)
    previous_review_id:str|None=None


def save_review(result):
    result={**result,'id':uuid.uuid4().hex,'created_at':datetime.now(timezone.utc).isoformat()}
    initialize()
    with host().connect() as con:con.execute('INSERT INTO reviews VALUES (?,?,?)',(result['id'],json.dumps(result,ensure_ascii=False),result['created_at']))
    return result


def get_review(review_id):
    initialize()
    with host().connect() as con:r=con.execute('SELECT result FROM reviews WHERE id=?',(review_id,)).fetchone()
    if not r:raise HTTPException(404,'复盘不存在。')
    return json.loads(r['result'])


@router.post('/reviews')
def create_review(body:ReviewBody):
    selected={kind:get_source(getattr(body,kind+'_id')) for kind in SCHEMAS}
    if any(meta['kind']!=kind for kind,meta in selected.items()):raise AnalysisError('三个输入必须分别是销售、目标、日运营表。')
    task=body.model_dump(exclude={'sales_id','targets_id','operations_id'})
    if body.previous_review_id:
        old=get_review(body.previous_review_id)
        if old['metric']!=body.metric or old['currency']!=selected['sales']['currency']:raise AnalysisError('前次复盘指标或单位不同。')
        if old['period']['end']>=body.start:raise AnalysisError('前次复盘必须早于本期。')
        if body.previous_start!=old['period']['start'] or body.previous_end!=old['period']['end']:raise AnalysisError('基期须等于所关联的前次复盘期间。')
    result=build_review(selected,{kind:load_source(meta['id']) for kind,meta in selected.items()},task)
    if body.previous_review_id:
        old=get_review(body.previous_review_id)
        old_map={(r['store'],r['sku']):r for r in old['rows']}
        result['followup']={'previous_review_id':old['id'],'previous_title':old['task']['title'],'observations':[{'store':r['store'],'sku':r['sku'],'previous_shortfall_cents':old_map[(r['store'],r['sku'])]['shortfall_cents'],'current_shortfall_cents':r['shortfall_cents']} for r in result['rows'] if (r['store'],r['sku']) in old_map], 'note':'前后观察不证明措施有效；目标版本可能不同，请核查。'}
    return save_review(result)


@router.get('/reviews')
def history():
    initialize()
    with host().connect() as con:
        return [{'id':r['id'],'title':r['task']['title'],'period':r['period'],'created_at':r['created_at'],'can_evaluate':r['readiness']['can_evaluate'],'report':bool(r.get('report'))} for r in [json.loads(x['result']) for x in con.execute('SELECT result FROM reviews ORDER BY rowid DESC LIMIT 50')]]


@router.get('/reviews/{review_id}')
def detail(review_id:str):return get_review(review_id)


@router.get('/reviews/{review_id}/export')
def export(review_id:str):return Response(json.dumps(get_review(review_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':'attachment; filename="review.json"'})


@router.get('/reviews/{review_id}/xlsx')
def excel_export(review_id:str,store:str=Query('',max_length=120),sku:str=Query('',max_length=120)):
    r=get_review(review_id)
    return Response(review_workbook(r,store,sku),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="review-{r["period"]["start"]}.xlsx"'})


def evidence_frame(review_id,evidence_id,kind):
    r=get_review(review_id)
    if kind not in SCHEMAS:raise AnalysisError('未知证据类型。')
    fact=next((f for f in r['facts'] if f['id']==evidence_id),None)
    if not fact:raise AnalysisError('证据不存在。')
    row=next((row for row in r['rows'] if row['evidence_id']==evidence_id),None)
    frame=load_source(r['sources'][kind]['id']);task=r['task']
    if row is not None:frame=frame[(frame.store==row['store'])&(frame.sku==row['sku'])]
    if kind=='targets':frame=frame[(frame.start==task['start'])&(frame.end==task['end'])&(frame.metric==r['metric'])]
    else:
        current=frame[(frame.date>=task['start'])&(frame.date<=task['end'])].assign(analysis_period='current')
        previous=frame[(frame.date>=task['previous_start'])&(frame.date<=task['previous_end'])].assign(analysis_period='previous') if kind=='sales' and row is not None and task.get('previous_start') else frame.iloc[:0]
        frame=pd.concat([current,previous],ignore_index=True)
    return frame,{'fact':fact,'investigation':row,'source':r['sources'][kind],'review_id':r['id'],'currency':r['currency'],'kind':kind,'note':'源记录序号含表头为1；目标/运营证据是独立来源，引用不证明因果。'}


@router.get('/reviews/{review_id}/evidence/{evidence_id}')
def evidence(review_id:str,evidence_id:str,kind:str='sales',offset:int=Query(0,ge=0),limit:int=Query(25,ge=1,le=100)):
    frame,context=evidence_frame(review_id,evidence_id,kind)
    return {**context,'rows':frame.iloc[offset:offset+limit].to_dict('records'),'total':len(frame),'offset':offset,'limit':limit}


@router.get('/reviews/{review_id}/evidence/{evidence_id}/export')
def evidence_export(review_id:str,evidence_id:str,kind:str='sales'):
    frame,_=evidence_frame(review_id,evidence_id,kind)
    return csv_response(frame,'review-'+kind+'-evidence.csv')


@router.post('/reviews/{review_id}/brief')
async def brief(review_id:str):
    r=get_review(review_id)
    if not r['readiness']['can_evaluate'] or not r['readiness']['overall_target_complete']:raise AnalysisError('关键数据或目标范围不完整，先解决核对问题再生成经营摘要。')
    return save_review({**r,'parent_review_id':r['id'],'brief':await generate_brief(r)})


class ReportBody(BaseModel):
    facts_note:str=Field(default='',max_length=2500)
    hypothesis:str=Field(default='',max_length=1500)
    next_check:str=Field(default='',max_length=1500)


@router.post('/reviews/{review_id}/report')
def report(review_id:str,body:ReportBody):
    r=get_review(review_id)
    # The same parent and content identify one report, including retry after a
    # lost response. Distinct notes create distinct immutable versions.
    notes = body.model_dump()
    key = hashlib.sha256(json.dumps({'parent':review_id,'report':notes},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:32]
    result = {**r,'id':key,'created_at':datetime.now(timezone.utc).isoformat(),
              'parent_review_id':review_id,'report':notes}
    with host().connect() as con:
        con.execute('INSERT OR IGNORE INTO reviews VALUES (?,?,?)',
                    (key,json.dumps(result,ensure_ascii=False),result['created_at']))
    return get_review(key)


@router.post('/demo')
def demo():
    selected={}
    for kind,blob in demo_blobs().items():
        digest=hashlib.sha256(blob).hexdigest()
        existing=next((m for m in sources_list() if m['kind']==kind and m['file_sha256']==digest and m['source'].startswith('明确模拟')) ,None)
        selected[kind]=existing or store_source(blob,{'sales':'两店商品交易','targets':'两店周目标','operations':'两店日运营'}[kind]+'.csv',kind,{},'模拟 · '+{'sales':'三周两店交易','targets':'三周商品目标','operations':'三周日运营'}[kind],'明确模拟：项目生成的三表业务演示，非商家业绩、预算或实际可售记录。','元')
    return create_review(ReviewBody(**{kind+'_id':meta['id'] for kind,meta in selected.items()},**demo_task()))


@router.get('/templates/{kind}')
def template(kind:str):
    if kind not in SCHEMAS:raise AnalysisError('未知模板。')
    return csv_response(pd.DataFrame(columns=SCHEMAS[kind].values()),'review-'+kind+'-template.csv')


@router.get('/examples/{kind}')
def example(kind:str):
    if kind not in SCHEMAS:raise AnalysisError('未知模板。')
    return Response(demo_blobs()[kind],media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':f'attachment; filename="SIMULATED-{kind}.csv"'})


@router.get('/reviews/{review_id}/report',response_class=HTMLResponse)
def report_page(review_id:str):
    r=get_review(review_id);esc=html.escape
    def amount(v):return '不可计算' if v is None else f'{v/100:,.2f} '+esc(r['currency'])
    states={'met':'达标','shortfall':'未达标','missing_target':'未配置目标','unknown_actual':'实际未知','unconfirmed_handover':'待完整交接确认','zero_target':'零目标比率不可计算','blocked':'关键问题待解决'}
    rows=''.join(f"<tr><td>{esc(x['store'])}/{esc(x['sku'])} {esc(x['name'])}</td><td>{amount(x['actual_cents'])}</td><td>{amount(x['target_cents'])}</td><td>{amount(x['difference_cents'])}</td><td>{x['attainment'] if x['attainment'] is not None else '不可计算'}</td><td>{states[x['status']]} [{esc(x['evidence_id'])}]</td></tr>" for x in r['rows'][:200])
    context_rows=''.join(f"<tr><td>{esc(x['store'])}/{esc(x['sku'])}</td><td>{amount(x['previous_cents'])} → {amount(x['actual_cents'])}</td><td>{amount(x['current']['positive_cents'])} / {amount(x['current']['negative_cents'])}</td><td>{x['operations']['observed_days']} / {x['operations']['calendar_days']}天</td><td>{x['operations']['open_minutes']} / {x['operations']['unavailable_minutes']}分钟</td><td>{esc(','.join(x['operations']['missing_days']) or '无')}</td></tr>" for x in r['rows'][:200])
    context_html=f"<h2>金额与运营上下文（不证明因果）</h2><div style='overflow:auto'><table><tr><th>门店/SKU</th><th>基期→本期实际</th><th>本期正向/负向</th><th>运营有记录/请求天数</th><th>已记录营业/不可售</th><th>缺失日期</th></tr>{context_rows}</table></div><p>报告显示前200个组合，完整{len(r['rows'])}个组合保留在JSON。缺失日不是零不可售，分钟仅汇总已提供记录。</p>"
    sources=''.join(f"<li>{esc(m['name'])}：{esc(m['source'])}<br>SHA：{esc(m['file_sha256'])}；原始{m['quality']['input_rows']} / 有效{m['quality']['valid_rows']} / 隔离{m['quality']['rejected_rows']} / 重复参与{m['quality']['duplicate_participants']}</li>" for m in r['sources'].values())
    issues=''.join('<li>'+esc(n)+'</li>' for n in dict.fromkeys(r['readiness']['blockers']+r['readiness']['warnings']+r['notices']))
    notes=r.get('report',{});note_html=''.join(f"<h2>{title}</h2><pre>{esc(notes.get(key,'') or '未填写')}</pre>" for key,title in [('facts_note','复盘说明（可能包含系统填入事实）'),('hypothesis','原因假设（尚未验证）'),('next_check','下一轮核查计划（不代表已执行）')])
    brief_html=''
    if r.get('brief',{}).get('status')=='validated':
        for key,title in [('facts','AI选择的计算事实'),('hypotheses','AI待核查解释'),('next_checks','AI建议核查')]:brief_html+='<h2>'+title+'</h2><ul>'+''.join('<li>'+esc(item['text'])+' ['+esc(','.join(item['evidence_ids']))+']</li>' for item in r['brief'][key])+'</ul>'
    status='核对可继续；仍须阅读限制' if r['readiness']['can_evaluate'] else '交接声明或关键数据尚未就绪，仅展示已提供记录，暂不评价达成'
    attainment='不可计算' if r['totals']['attainment'] is None else str(r['totals']['attainment'])+'%'
    followup_html=''
    if r.get('followup'):
        f=r['followup']
        observations=''.join(f"<li>{esc(x['store'])}/{esc(x['sku'])}：目标缺口 {amount(x['previous_shortfall_cents'])} → {amount(x['current_shortfall_cents'])}</li>" for x in f['observations'][:200])
        followup_html=f"<h2>前次关注与本次观察</h2><p>{esc(f['previous_title'])}；{esc(f['note'])}</p><ul>{observations}</ul><a href='/api/ops/reviews/{esc(f['previous_review_id'])}/report'>查看前次快照</a>"
    return f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(r['task']['title'])}</title><style>body{{font:15px/1.7 system-ui;max-width:1050px;margin:30px auto;padding:24px;color:#193f38;overflow-wrap:anywhere}}table{{width:100%;min-width:640px;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ddd}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}li{{overflow-wrap:anywhere}}@media print{{button{{display:none}}body{{margin:0;padding:0}}}}</style><button onclick="window.print()">打印 / 保存PDF</button><h1>{esc(r['task']['title'])}</h1><p>{esc(r['task']['question'])}</p><p>{esc(r['period']['start'])}—{esc(r['period']['end'])}；指标：{esc(r['definition']['label'])}</p><p>{esc(r['definition']['definition'])}</p><h2>数据核对</h2><p>{status}</p><ul>{issues}</ul><p>关联前金额 {amount(r['readiness']['controls']['before_join_cents'])}；对齐后金额 {amount(r['readiness']['controls']['after_grain_alignment_cents'])}；完整交接声明：{esc(r['task']['confirmation_note'] or '未确认')}</p><h2>目标与实际</h2><p>有效记录实际 {amount(r['totals']['actual_cents'])}；同范围目标 {amount(r['totals']['target_cents'])}；达成率 {attainment}</p><div style="overflow:auto"><table><tr><th>门店/SKU/名称</th><th>实际</th><th>目标</th><th>实际减目标</th><th>达成%</th><th>证据</th></tr>{rows}</table></div>{context_html}{followup_html}{brief_html}{note_html}<h2>来源与版本</h2><ul>{sources}</ul><p>复盘ID：{r['id']}；生成时间：{esc(r['created_at'])}。引用可追踪不证明因果；模拟数据/Agent检查不等于个人实操或商家业绩。</p><a href="/api/ops/reviews/{r['id']}/export">下载完整快照JSON</a></html>'''
