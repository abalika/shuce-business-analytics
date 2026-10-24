"""Fresh local HTTP workflow + raw CSV/SQL controls; no API account or network required."""
import argparse
import csv
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate(output):
    if output.exists():
        raise ValueError('Output directory must be new; previous evidence is never overwritten')
    stage = ROOT / 'work' / ('offline-eval-' + uuid.uuid4().hex[:10])
    os.environ['SHUCE_DATA_DIR'] = str(stage / 'data')
    os.environ['SHUCE_OFFLINE'] = '1'
    # Deliberately present credentials demonstrate that offline mode wins.
    os.environ['OPENAI_API_KEY'] = 'offline-test-unused'
    os.environ['OPENAI_BASE_URL'] = 'http://provider.invalid/v1'
    os.environ['OPENAI_MODEL'] = 'offline-test-unused'
    from fastapi.testclient import TestClient
    from app.main import app
    from app.review_demo import demo_blobs
    from app.version import VERSION
    output.mkdir(parents=True)
    def require(condition, message):
        if not condition: raise RuntimeError(message)
    def store(name, content):
        (output / name).write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
    network_attempts = []
    def deny_network(*a, **kw):
        network_attempts.append(True)
        raise AssertionError('Offline evaluation attempted model access')
    with TestClient(app, headers={'X-Shuce-Client':'1'}) as client, patch('app.ai.httpx.AsyncClient', deny_network):
        def get(path):
            response = client.get(path)
            require(response.status_code == 200, 'GET workflow failed: ' + path)
            return response
        def post(path, **kwargs):
            response = client.post(path, **kwargs)
            require(response.status_code == 200, 'POST workflow failed: ' + path)
            return response.json()
        require(get('/api/status').json()['ai']['offline'], 'Offline flag missing')
        review = post('/api/ops/demo')
        require((review['totals']['actual_cents'], review['totals']['target_cents']) == (286600, 266000), 'Demo control mismatch')
        require((review['totals']['met_categories'],review['totals']['evaluable_categories']) == (7,8), 'Target categories mismatch')
        # Independent raw CSV to in-memory SQL, bypassing app normalization/aggregation.
        con = sqlite3.connect(':memory:')
        con.execute('CREATE TABLE raw_sales(store TEXT,sku TEXT,day TEXT,cents INTEGER)')
        blobs = demo_blobs()
        for row in csv.DictReader(io.StringIO(blobs['sales'].decode('utf-8-sig'))):
            cents = int((Decimal(row['数量'])*Decimal(row['成交单价'])*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
            con.execute('INSERT INTO raw_sales VALUES(?,?,?,?)', (row['门店ID'],row['商品SKU'],row['业务日期'],cents))
        controls = {(store,sku): cents for store,sku,cents in con.execute("SELECT store,sku,SUM(cents) FROM raw_sales WHERE day BETWEEN '2026-09-21' AND '2026-09-27' GROUP BY store,sku")}
        con.close()
        require(all(controls[(r['store'],r['sku'])] == r['actual_cents'] for r in review['rows']), 'Raw SQL category mismatch')
        for kind,blob in blobs.items(): store('SIMULATED-' + kind + '.csv',blob)
        url = '/api/ops/reviews/' + review['id']
        saved = post(url + '/report', json={'facts_note':'模拟：本期净交易额2866元，目标2660元；八组中七组达标。', 'hypothesis':'可能与可售情况或交易数量变化有关，尚需核查，不能认定因果。', 'next_check':'逐一核对南门拿铁交易、目标依据及缺失运营日。'})
        saved_url = '/api/ops/reviews/' + saved['id']
        brief = post(saved_url + '/brief')
        require(brief['brief']['status'] == 'not_configured', 'Offline brief must not impersonate AI')
        require(get(saved_url).json()['report'] == saved['report'], 'Saved snapshot changed')
        page = get(saved_url + '/report').text
        require(not re.search(r'<(?:script|link|img)\b[^>]*(?:src|href)=["\']https?://', page, re.I), 'Report depends on network assets')
        page = re.sub(r'href="/api/ops/reviews/[^\"]+/export"', 'href="review.json"', page)
        page = page.replace('<h1>', '<p role="note">预置演示数据 / 非实时：项目生成的模拟业务与规则计算结果，不含实时模型输出。</p><h1>', 1)
        require('预置演示数据 / 非实时' in page and 'href="/api/' not in page, 'Static report must be labeled and use local downloads')
        store('report.html', page)
        store('review.json', get(saved_url + '/export').content)
        store('review.xlsx', get(saved_url + '/xlsx').content)
        from openpyxl import load_workbook
        require(len(load_workbook(io.BytesIO((output/'review.xlsx').read_bytes())).sheetnames) == 3, 'Excel sheet contract changed')
        for kind in blobs:
            response = get(saved_url + '/evidence/E1/export?kind=' + kind)
            require(len(response.content) > 20, 'Evidence missing')
            store('evidence-' + kind + '.csv', response.content)
        # Legacy rule-only path is part of the offline contract too.
        data = post('/api/demo/orders')
        result = post('/api/analyze',json={'dataset_id':data['id'],'question':'净交易额按月趋势如何？'})
        require(result['trace']['provider'] == 'rule', 'Offline question used a model')
        require(not network_attempts, 'Provider access detected')
    entries = ['report.html','review.xlsx','review.json'] + ['SIMULATED-'+k+'.csv' for k in blobs] + ['evidence-'+k+'.csv' for k in blobs]
    links = ''.join('<li><a href="'+name+'">'+name+'</a></li>' for name in entries)
    store('index.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>数策离线演示</title><body style="max-width:850px;margin:40px auto;padding:0 20px;font:18px/1.7 system-ui"><h1>数策：两店经营复盘</h1><p>预置演示数据 / 非实时：以下全部为项目生成的模拟数据，不含实时模型结果，无需服务、API或网络。双击报告可查看；数据文件与三源证据用于核查。</p><p>本期2026-09-21至27：净交易额2866元，目标2660元，八组中七组达标。南门拿铁312元/500元，缺口188元。运营缺日与不可售分钟只是核查线索，不能证明损失或因果。</p><ol>'+links+'</ol><p>交互版：在项目根目录运行离线演示.cmd。静态报告不会因操作而更新，Excel不回写系统。</p></body></html>')
    result = {'ok':True,'version':VERSION,'date':datetime.now().isoformat(),'work_data':str(stage/'data'), 'offline':True,'provider_network_attempts':len(network_attempts),'model_generation_requests':0,'actual_cents':286600,'target_cents':266000,'raw_sql_categories_checked':len(controls),'saved_snapshot_reopened':True,'three_evidence_exports':True,'xlsx_verified':True,'static_html_external_assets':False,'browser_inspected':False,'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()}}
    store('evaluation.json', json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result,ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='New output folder; never overwrite evidence')
    args = parser.parse_args()
    try: evaluate(args.out.resolve())
    except Exception as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'detail':'Offline workflow failed; inspect local test evidence. No production data was replaced.'}))
        raise SystemExit(1)
