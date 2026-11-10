import io,json
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from app import main
from app import reviews
from app.review_demo import demo_blobs,demo_task
from app.review_engine import normalize_source,mapping_for,build_review,SCHEMAS


def tables():
    return {kind:pd.read_csv(io.BytesIO(blob),dtype=str,keep_default_na=False,encoding='utf-8-sig') for kind,blob in demo_blobs().items()}


def calculate(raw=None,task=None):
    raw=raw or tables();frames={};metas={}
    for kind,f in raw.items():
        normalized,q,d=normalize_source(f,kind,mapping_for(f,kind));frames[kind]=normalized
        metas[kind]={'id':kind,'name':kind,'quality':q,'currency':'元','source':'明确模拟'}
    return build_review(metas,frames,{**demo_task(),**(task or {})})


def row(r,store='S01',sku='LATTE'):return next(x for x in r['rows'] if x['store']==store and x['sku']==sku)


def test_three_tables_hand_control_and_name_history():
    r=calculate();assert r['readiness']['can_evaluate'] and r['readiness']['overall_target_complete']
    assert r['totals']['actual_cents']==286600 and r['totals']['target_cents']==266000
    assert r['totals']['met_categories']==7 and r['totals']['evaluable_categories']==8
    x=row(r);assert x['actual_cents']==31200 and x['target_cents']==50000 and x['shortfall_cents']==18800 and x['attainment']==62.4
    assert x['previous_cents']==52000 and x['period_change_cents']==-20800
    assert x['names']==['拿铁咖啡','经典拿铁']
    assert x['current']['positive_quantity']=='14' and x['current']['negative_quantity']=='2'
    assert x['current']['positive_cents']==36400 and x['current']['negative_cents']==-5200
    assert x['operations']['observed_days']==6 and x['operations']['missing_days']==['2026-09-24'] and x['operations']['unavailable_minutes']==180
    assert r['readiness']['controls']['reconciled']


def test_orders_are_scoped_by_store_not_lines():
    r=calculate();assert r['totals']['orders']==77 and r['totals']['records']==149
    assert r['totals']['orders']<r['totals']['records']


def test_missing_target_does_not_default_to_zero_or_overall():
    raw=tables();t=raw['targets'];raw['targets']=t[~((t['门店ID']=='S01')&(t['商品SKU']=='LATTE')&(t['开始日期']=='2026-09-21'))]
    r=calculate(raw);assert row(r)['status']=='missing_target' and row(r)['target_cents'] is None
    assert r['totals']['target_cents'] is None and r['totals']['attainment'] is None


def test_missing_actual_is_unknown_without_confirmation_then_zero_with_declaration():
    raw=tables();s=raw['sales'];raw['sales']=s[~((s['门店ID']=='S01')&(s['商品SKU']=='LATTE')&(s['业务日期']>='2026-09-21')&(s['业务日期']<='2026-09-27'))]
    r=calculate(raw,{'confirmed_complete':False,'confirmation_note':''});assert row(r)['actual_cents'] is None and row(r)['status']=='unknown_actual'
    assert r['totals']['attainment'] is None
    confirmed=calculate(raw);assert row(confirmed)['actual_cents']==0 and row(confirmed)['status']=='shortfall'


def test_nonempty_records_do_not_imply_full_period_handover():
    r=calculate(task={'confirmed_complete':False,'confirmation_note':''})
    assert r['totals']['actual_cents']==286600 # valid supplied records remain visible
    assert not r['readiness']['can_evaluate'] and r['totals']['attainment'] is None
    assert all(x['status']=='unconfirmed_handover' and x['attainment'] is None and x['difference_cents'] is None for x in r['rows'])


@pytest.mark.parametrize('kind',['sales','targets','operations'])
def test_duplicate_keys_are_blocked_not_dropped(kind):
    raw=tables();raw[kind]=pd.concat([raw[kind],raw[kind].iloc[:1]],ignore_index=True)
    r=calculate(raw);assert not r['readiness']['can_evaluate']
    assert r['totals']['target_cents'] is None and all(x['attainment'] is None and x['difference_cents'] is None for x in r['rows'])


def test_monthly_goal_is_not_prorated():
    raw=tables();t=raw['targets'];mask=(t['开始日期']=='2026-09-21');t.loc[mask,'开始日期']='2026-09-01';t.loc[mask,'结束日期']='2026-09-30'
    r=calculate(raw);assert not r['readiness']['can_evaluate'] and any('期间' in n for n in r['readiness']['blockers'])


def test_currency_conflict_is_blocked():
    raw=tables();raw['targets'].loc[0,'单位']='USD'
    r=calculate(raw,{'start':'2026-09-14','end':'2026-09-20','previous_start':None,'previous_end':None})
    assert not r['readiness']['can_evaluate']


def test_zero_goal_and_negative_actual_are_not_fake_zero():
    raw=tables();t=raw['targets'];t.loc[(t['门店ID']=='S01')&(t['商品SKU']=='LATTE'),'目标值']='0'
    r=calculate(raw);assert row(r)['status']=='zero_target' and row(r)['attainment'] is None
    s=raw['sales'];raw['sales']=s[~((s['门店ID']=='S01')&(s['商品SKU']=='LATTE')&(s['数量']=='1')&(s['业务日期']>='2026-09-21')&(s['业务日期']<='2026-09-27'))]
    r=calculate(raw);assert row(r)['actual_cents']==-5200


def test_same_name_different_sku_stays_separate():
    raw=tables();raw['sales'].loc[raw['sales']['商品SKU']=='TEA','商品名称']='美式咖啡'
    r=calculate(raw);assert len(r['rows'])==8 and row(r,sku='TEA')['actual_cents']==33600


def test_name_history_does_not_use_later_cycle_labels():
    raw=tables();s=raw['sales'];s.loc[(s['商品SKU']=='LATTE')&(s['业务日期']>'2026-09-27'),'商品名称']='后期才启用的名称'
    r=calculate(raw)
    assert row(r)['name']=='经典拿铁' and row(r)['names']==['拿铁咖啡','经典拿铁']


def test_bad_operations_minutes_block_and_closed_sales_warn_but_refunds_are_allowed():
    raw=tables();raw['operations'].loc[0,'不可售分钟']='601'
    assert not calculate(raw)['readiness']['can_evaluate']
    raw=tables();o=raw['operations'];o.loc[(o['门店ID']=='S01')&(o['商品SKU']=='LATTE')&(o['日期']=='2026-09-27'),'营业分钟']='0'
    r=calculate(raw);assert row(r)['operations']['closed_sale_dates']==['2026-09-27']
    s=raw['sales'];raw['sales']=s[~((s['门店ID']=='S01')&(s['商品SKU']=='LATTE')&(s['业务日期']=='2026-09-27')&(s['数量']=='1'))]
    assert row(calculate(raw))['operations']['closed_sale_dates']==[]


def test_scalar_date_overlap_and_confirmation_guards():
    from app.engine import AnalysisError
    for task in [{'start':'2026-09-99'},{'previous_end':'2026-09-21'},{'confirmed_complete':True,'confirmation_note':''},{'metric':'profit'}]:
        with pytest.raises(AnalysisError):calculate(task=task)


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DATA',tmp_path);monkeypatch.setattr(main,'DB',tmp_path/'index.sqlite3');main.initialize()
    with TestClient(main.app,base_url='http://127.0.0.1',headers={'X-Shuce-Client':'1'}) as c:yield c


def test_api_evidence_three_origins_and_csv(client):
    r=client.post('/api/ops/demo').json();assert r['totals']['actual_cents']==286600
    x=row(r)
    expected={'sales':37,'targets':1,'operations':6}
    for kind,total in expected.items():
        url=f"/api/ops/reviews/{r['id']}/evidence/{x['evidence_id']}?kind={kind}"
        e=client.get(url).json();assert e['total']==total and e['source']['id']==r['sources'][kind]['id']
        assert all(rec['store']=='S01' and rec['sku']=='LATTE' for rec in e['rows'])
        assert client.get(url.split('?')[0]+'/export?kind='+kind).status_code==200
    assert client.get(f"/api/ops/reviews/{r['id']}/evidence/BAD").status_code==422
    assert client.get(f"/api/ops/reviews/{r['id']}/evidence/E1?offset=-1").status_code==422


def test_evidence_reads_the_calculated_period_even_for_old_task_dates(client):
    demo = client.post('/api/ops/demo').json()
    source_ids = {kind + '_id': meta['id'] for kind, meta in demo['sources'].items()}
    task = {**demo['task'], **source_ids}
    for field in ('start', 'end', 'previous_start', 'previous_end'):
        task[field] = ' ' + task[field] + ' 00:00:00 '
    response = client.post('/api/ops/reviews', json=task)
    assert response.status_code == 200
    review = response.json()
    assert review['totals'] == demo['totals']
    assert review['task']['start'] == demo['period']['start']
    # Simulate an existing snapshot saved before canonical task dates were persisted.
    old = reviews.save_review({**review, 'task': task})
    for snapshot in (review, old):
        row = next(row for row in snapshot['rows'] if row['store'] == 'S01' and row['sku'] == 'LATTE')
        url = f"/api/ops/reviews/{snapshot['id']}/evidence/{row['evidence_id']}"
        sales = client.get(url + '?kind=sales&limit=100').json()['rows']
        assert {r['analysis_period'] for r in sales} == {'current', 'previous'}
        assert sum(r['amount'] for r in sales if r['analysis_period'] == 'current') == row['actual_cents']
        targets = client.get(url + '?kind=targets').json()['rows']
        assert [r['target_cents'] for r in targets] == [row['target_cents']]
        ops = client.get(url + '?kind=operations').json()['rows']
        assert len(ops) == row['operations']['observed_days']


def test_report_append_versions_and_reopen_persistence(client):
    r=client.post('/api/ops/demo').json()
    saved=client.post(f"/api/ops/reviews/{r['id']}/report",json={'facts_note':'核对目标缺口','hypothesis':'<script>alert(1)</script>','next_check':'计划核查，未执行'}).json()
    assert saved['id']!=r['id'] and saved['parent_review_id']==r['id']
    assert 'report' not in client.get('/api/ops/reviews/'+r['id']).json()
    html=client.get('/api/ops/reviews/'+saved['id']+'/report').text
    assert '<script>alert(1)' not in html and '&lt;script&gt;' in html and '目标' in html
    reviews.initialize();assert client.get('/api/ops/reviews/'+saved['id']).json()['report']['next_check']=='计划核查，未执行'
    assert client.get('/api/ops/reviews/'+saved['id']+'/export').json()==saved


def test_followup_requires_matching_previous_period_and_keeps_old(client):
    r=client.post('/api/ops/demo').json();task={**demo_task(),'start':'2026-09-28','end':'2026-10-04','previous_start':'2026-09-21','previous_end':'2026-09-27','previous_review_id':r['id']}
    ids={k+'_id':m['id'] for k,m in r['sources'].items()}
    follow=client.post('/api/ops/reviews',json={**task,**ids}).json()
    assert follow['followup']['previous_review_id']==r['id']
    assert row(follow)['actual_cents']==52000 and row(follow)['shortfall_cents']==0
    assert '前次关注与本次观察' in client.get('/api/ops/reviews/'+follow['id']+'/report').text
    assert client.get('/api/ops/reviews/'+r['id']).json()==r
    assert client.post('/api/ops/reviews',json={**task,**ids,'previous_start':'2026-09-20'}).status_code==422


def test_import_mapping_and_unknown_kinds(client):
    blob=demo_blobs()['targets'];raw=pd.read_csv(io.BytesIO(blob),encoding='utf-8-sig',dtype=str)
    raw.columns=['x'+str(i) for i in range(len(raw.columns))];out=raw.to_csv(index=False).encode('utf-8')
    preview=client.post('/api/ops/preview',files={'file':('renamed.csv',out)},data={'kind':'targets'}).json();assert preview['rows']==24
    mapped=dict(zip(SCHEMAS['targets'],raw.columns))
    imported=client.post('/api/ops/sources',files={'file':('renamed.csv',out)},data={'kind':'targets','mapping':json.dumps(mapped)});assert imported.status_code==200
    assert imported.json()['quality']['valid_rows']==24
    assert client.post('/api/ops/preview',files={'file':('a.csv',blob)},data={'kind':'other'}).status_code==422


def test_blocked_review_cannot_generate_ai_brief(client):
    r=client.post('/api/ops/demo').json()
    raw=tables()['targets'];raw=pd.concat([raw,raw.iloc[:1]])
    meta=client.post('/api/ops/sources',files={'file':('dup.csv',raw.to_csv(index=False).encode('utf-8'))},data={'kind':'targets'}).json()
    task={**demo_task(),**{k+'_id':m['id'] for k,m in r['sources'].items()},'targets_id':meta['id']}
    bad=client.post('/api/ops/reviews',json=task).json()
    assert client.post('/api/ops/reviews/'+bad['id']+'/brief').status_code==422


def test_local_write_guard_and_export_formula_protection(client):
    assert client.post('/api/ops/demo',headers={'x-shuce-client':''}).status_code==403
    r=client.post('/api/ops/demo').json()
    assert client.get('/api/ops/reviews/missing').status_code==404
    assert client.get('/api/ops/sources/'+r['sources']['sales']['id']+'/original').status_code==200
    assert client.get('/').status_code==200 and '经营复盘' in client.get('/').text
    assert client.get('/explore').status_code==200


def test_auxiliary_mapping_must_be_object_and_role_mismatch_rejected(client):
    blob=demo_blobs()['targets']
    assert client.post('/api/ops/sources',files={'file':('t.csv',blob)},data={'kind':'targets','mapping':'[]'}).status_code==422
    r=client.post('/api/ops/demo').json()
    args={**demo_task(),**{k+'_id':m['id'] for k,m in r['sources'].items()}}
    args['sales_id']=args['targets_id']
    assert client.post('/api/ops/reviews',json=args).status_code==422


def test_evidence_csv_escapes_formula_name_and_report_preserves_operations(client):
    raw=tables()['sales'];raw.loc[raw['商品SKU']=='LATTE','商品名称']='=HYPERLINK(1)'
    meta=client.post('/api/ops/sources',files={'file':('s.csv',raw.to_csv(index=False).encode('utf-8'))},data={'kind':'sales'}).json()
    r=client.post('/api/ops/demo').json()
    args={**demo_task(),**{k+'_id':m['id'] for k,m in r['sources'].items()},'sales_id':meta['id']}
    custom=client.post('/api/ops/reviews',json=args).json();x=row(custom)
    export=client.get(f"/api/ops/reviews/{custom['id']}/evidence/{x['evidence_id']}/export")
    assert "'=HYPERLINK(1)" in export.text
    report=client.get(f"/api/ops/reviews/{custom['id']}/report").text
    assert '2026-09-24' in report and '已记录营业/不可售' in report and '前200' in report


def test_bad_xlsx_and_missing_ids_do_not_crash(client):
    assert client.post('/api/ops/preview',files={'file':('bad.xlsx',b'not-a-zip')},data={'kind':'sales'}).status_code==422
    assert client.get('/api/ops/sources/missing/original').status_code==404


def workbook_download(client, review_id, query=''):
    from openpyxl import load_workbook
    response=client.get('/api/ops/reviews/'+review_id+'/xlsx'+query)
    assert response.status_code==200
    assert response.headers['content-type'].startswith('application/vnd.openxmlformats-officedocument')
    return load_workbook(io.BytesIO(response.content))


def test_excel_full_scope_numeric_values_sources_and_immutable_report(client):
    r=client.post('/api/ops/demo').json()
    saved=client.post('/api/ops/reviews/'+r['id']+'/report',json={'facts_note':'核查记录已保存','hypothesis':'尚未验证','next_check':'补运营记录'}).json()
    wb=workbook_download(client,saved['id'])
    assert wb.sheetnames==['经营复盘','核查上下文','来源与说明']
    s=wb['经营复盘'];assert s['B4'].value==2866 and s['D4'].value==2660
    assert s['F4'].value==pytest.approx(2866/2660) and s['F4'].number_format=='0.00%'
    details=list(s.iter_rows(min_row=7,max_row=14,values_only=True));assert len(details)==8
    x=next(v for v in details if v[:2]==('S01','LATTE'))
    assert x[3:9]==(312,500,-188,188,0.624,-208)
    assert s['B17'].value=='核查记录已保存'
    c=wb['核查上下文'];x=next(v for v in c.iter_rows(min_row=5,values_only=True) if v[:2]==('S01','LATTE'))
    assert x[10:15]==(6,7,3600,180,'2026-09-24')
    source_text=' '.join(str(v) for cells in wb['来源与说明'].values for v in cells if v is not None)
    assert all(m['file_sha256'] in source_text and m['id'] in source_text for m in r['sources'].values())
    assert client.get('/api/ops/reviews/'+r['id']).json()==r
    assert client.get('/api/ops/reviews/'+saved['id']).json()==saved
    assert s.auto_filter.ref=='A6:K14' and s.freeze_panes=='D7'


def test_excel_filter_does_not_relabel_whole_task_totals(client):
    r=client.post('/api/ops/demo').json();wb=workbook_download(client,r['id'],'?store=S01&sku=LATTE')
    s=wb['经营复盘'];assert s.auto_filter.ref=='A6:K7' and s['B4'].value==2866
    assert '概览为全任务' in s['B3'].value and '匹配1 / 全部8' in s['B3'].value
    assert s['A7'].value=='S01' and s['B7'].value=='LATTE'
    assert client.get('/api/ops/reviews/'+r['id']+'/xlsx?store=not-found').status_code==422


def test_excel_unknown_and_zero_are_distinct(client):
    r=client.post('/api/ops/demo').json();ids={k+'_id':m['id'] for k,m in r['sources'].items()}
    unknown=client.post('/api/ops/reviews',json={**demo_task(),**ids,'confirmed_complete':False,'confirmation_note':''}).json()
    s=workbook_download(client,unknown['id'])['经营复盘']
    assert s['F4'].value is None
    assert all(v[5] is None and v[7] is None and v[9]=='待完整交接确认' for v in s.iter_rows(min_row=7,max_row=14,values_only=True))
    raw=tables()['targets'];raw.loc[(raw['门店ID']=='S01')&(raw['商品SKU']=='LATTE'),'目标值']='0'
    meta=client.post('/api/ops/sources',files={'file':('zero.csv',raw.to_csv(index=False).encode('utf-8'))},data={'kind':'targets'}).json()
    zero=client.post('/api/ops/reviews',json={**demo_task(),**ids,'targets_id':meta['id']}).json()
    s=workbook_download(client,zero['id'])['经营复盘']
    x=next(v for v in s.iter_rows(min_row=7,max_row=14,values_only=True) if v[:2]==('S01','LATTE'))
    assert x[4]==0 and x[7] is None and x[9]=='零目标，比率不可计算'


def test_excel_literal_text_and_more_than_display_limit(client):
    import copy
    from openpyxl import load_workbook
    from app.review_export import review_workbook
    r=client.post('/api/ops/demo').json();snapshot=copy.deepcopy(r)
    snapshot['task']['title']='=HYPERLINK("https://example.com")'
    snapshot['report']={'facts_note':'=1+1','hypothesis':'+123','next_check':'\x00未知'}
    snapshot['rows']=[{**r['rows'][0],'store':f'{i:03d}','sku':'=SKU'} for i in range(150)]
    s=load_workbook(io.BytesIO(review_workbook(snapshot)))['经营复盘']
    assert s.max_row==161 and s['A7'].value=='000' and s['A156'].value=='149'
    assert s['A1'].data_type=='s' and s['B159'].data_type=='s' and s['B7'].data_type=='s'
    assert s['B161'].value=='�未知' and not any(c.data_type=='f' for cells in s for c in cells)


def test_long_excel_comment_remains_visible_in_continuation_rows(client):
    from openpyxl import load_workbook
    from app.review_export import review_workbook
    r=client.post('/api/ops/demo').json()
    text='需要核对本期数据来源。'*200
    r['report']={'facts_note':text}
    sheet=load_workbook(io.BytesIO(review_workbook(r)))['经营复盘']
    parts=[cells[1] for cells in sheet.values if str(cells[0]).startswith('已保存复盘说明')]
    assert ''.join(parts)==text and len(parts)>1
    assert all(d.height<409 for d in sheet.row_dimensions.values())
    assert sheet.freeze_panes=='D7'


@pytest.mark.parametrize('kind',['sales','targets','operations'])
def test_downloaded_simulated_samples_match_import_contract(client,kind):
    response=client.get('/api/ops/examples/'+kind)
    assert response.status_code==200 and 'SIMULATED-' in response.headers['content-disposition']
    assert response.content==demo_blobs()[kind]
    frame=pd.read_csv(io.BytesIO(response.content),dtype=str,keep_default_na=False,encoding='utf-8-sig')
    normalized,quality,_=normalize_source(frame,kind,mapping_for(frame,kind))
    assert not quality['blocking'] and len(normalized)==len(frame)
    assert client.get('/api/ops/examples/other').status_code==422
