"""Regressions from the adversarial input and report audit, not demo-only checks."""
import io
import pandas as pd
import pytest

from app.engine import read_frame, normalize, infer_mapping
from app.review_engine import normalize_source, mapping_for
from app.review_demo import demo_task
from app import reviews
from tests.test_reviews import client, tables


def encode(frame, extension):
    if extension == 'csv':
        return frame.to_csv(index=False).encode('utf-8-sig')
    out = io.BytesIO()
    frame.to_excel(out, index=False)
    return out.getvalue()


@pytest.mark.parametrize('extension', ['csv', 'xlsx'])
def test_literal_na_headers_values_and_real_blanks_remain_distinct(extension):
    frame = pd.DataFrame({'NA':['NA','NULL','N/A','0001',''], 'NULL':['a','b','c','d','e']})
    actual = read_frame(encode(frame,extension), 'literal.'+extension)
    assert actual.to_dict('list') == frame.to_dict('list')


@pytest.mark.parametrize('extension', ['csv', 'xlsx'])
def test_three_source_identifier_roundtrip_and_evidence(client, extension):
    selected = {}
    for kind, frame in tables().items():
        frame['门店ID'] = frame['门店ID'].replace('S01','NA')
        frame['商品SKU'] = frame['商品SKU'].replace('LATTE','NULL')
        if kind == 'sales':
            frame['商品名称'] = frame['商品名称'].where(frame['商品SKU']!='NULL', 'N/A')
            frame.loc[0,'明细事件ID'] = 'NA'
            frame.loc[0,'订单ID'] = 'NULL'
        response = client.post('/api/ops/sources',files={'file':('literal.'+extension,encode(frame,extension))},data={'kind':kind})
        assert response.status_code == 200, response.text
        selected[kind] = response.json()
        assert not selected[kind]['quality']['blocking']
        loaded = reviews.load_source(selected[kind]['id'])
        assert set(loaded.store)=={'NA','S02'} and 'NULL' in set(loaded.sku)
    response = client.post('/api/ops/reviews',json={**demo_task(),**{k+'_id':v['id'] for k,v in selected.items()}})
    assert response.status_code == 200, response.text
    review = response.json()
    item = next(x for x in review['rows'] if x['store']=='NA' and x['sku']=='NULL')
    assert (item['actual_cents'],item['target_cents'],item['shortfall_cents']) == (31200,50000,18800)
    assert item['name']=='N/A' and review['totals']['actual_cents']==286600
    for kind in selected:
        evidence = client.get(f"/api/ops/reviews/{review['id']}/evidence/{item['evidence_id']}?kind={kind}").json()
        assert evidence['total'] > 0 and all(x['store']=='NA' and x['sku']=='NULL' for x in evidence['rows'])


@pytest.mark.parametrize('field,value', [('数量','1e999999999'),('数量','-1e999999999'),('成交单价','1e999999999')])
def test_out_of_range_sales_numbers_are_quarantined(client, field, value):
    frame = tables()['sales'].iloc[:2].copy()
    frame.loc[frame.index[0],field] = value
    response = client.post('/api/ops/sources',files={'file':('extreme.csv',encode(frame,'csv'))},data={'kind':'sales'})
    assert response.status_code==200, response.text
    quality = response.json()['quality']
    assert quality['blocking'] and quality['valid_rows']==1 and quality['rejected_rows']==1
    diagnostics = client.get('/api/ops/sources/'+response.json()['id']+'/diagnostics').text
    assert '超出支持范围' in diagnostics


@pytest.mark.parametrize('value', ['1e999999999','-1e999999999'])
def test_legacy_orders_extreme_quantity_is_quarantined(value):
    frame=pd.DataFrame([['2026-09-21','A','1','10','O1'],['2026-09-21','A',value,'10','O2']],columns=['日期','商品','数量','单价','订单编号'])
    normalized, quality = normalize(frame,'orders',infer_mapping(frame,'orders'))
    assert len(normalized)==1 and quality['rejected_rows']==1


@pytest.mark.parametrize('field', ['营业分钟','不可售分钟'])
def test_extreme_operations_minutes_rejected_before_integer_allocation(field):
    frame=tables()['operations'].iloc[:2].copy()
    frame.loc[frame.index[0],field]='1e999999999'
    normalized,quality,diagnostics=normalize_source(frame,'operations',mapping_for(frame,'operations'))
    assert len(normalized)==1 and quality['blocking'] and quality['rejected_rows']==1
    assert '0≤不可售≤营业≤1440' in diagnostics[0]['reason']


def test_reports_support_device_viewport_and_no_duplicate_warning(client):
    from openpyxl import load_workbook
    r=client.post('/api/ops/demo').json()
    warning=r['readiness']['warnings'][0]
    page=client.get('/api/ops/reviews/'+r['id']+'/report').text
    assert 'name="viewport" content="width=device-width,initial-scale=1"' in page
    assert page.count(warning)==1
    wb=load_workbook(io.BytesIO(client.get('/api/ops/reviews/'+r['id']+'/xlsx').content))
    assert sum(c.value==warning for cells in wb['来源与说明'] for c in cells)==1
    d=client.post('/api/demo/orders').json()
    a=client.post('/api/analyze',json={'dataset_id':d['id'],'plan':{'operation':'overview','metric':'amount'}}).json()
    report=client.post('/api/reports',json={'analysis_id':a['id']}).json()
    assert 'name="viewport" content="width=device-width,initial-scale=1"' in client.get(report['url']).text
