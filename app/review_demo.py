"""Three-table, three-period Chinese fixtures. Never customer results."""
import csv
import io
from datetime import date,timedelta
from .review_engine import SCHEMAS


def demo_blobs():
    rows={'sales':[],'targets':[],'operations':[]}
    items=[('AMERICANO','美式咖啡',18),('LATTE','拿铁咖啡',26),('TEA','柠檬茶',16),('CAKE','芝士蛋糕',22)]
    for week in range(3):
        start=date(2026,9,14)+timedelta(days=week*7);end=start+timedelta(days=6)
        for store in ['S01','S02']:
            counts=([3,3,3,3] if week==0 else [4,2,3,3] if week==1 else [4,3,3,3]) if store=='S01' else ([2,2,2,2] if week==0 else [3,2,2,2] if week==1 else [3,3,2,2])
            goal=[420,500,200,380] if store=='S01' else [340,350,200,270]
            for i,(sku,name,price) in enumerate(items):
                rows['targets'].append([store,sku,'净交易额','元',start.isoformat(),end.isoformat(),goal[i],'项目构造的周计划，用于验收；非真实预算'])
            for day in range(7):
                when=(start+timedelta(days=day)).isoformat();serial=0
                for i,(sku,name,price) in enumerate(items):
                    for j in range(counts[i]):
                        serial+=1
                        label='经典拿铁' if sku=='LATTE' and week>0 else name
                        rows['sales'].append([f'{when}-{serial}',f'{when}-{(serial-1)//2+1}',when,store,sku,label,1,price])
                    if not (store=='S01' and sku=='LATTE' and when=='2026-09-24'):
                        unavailable=120 if store=='S01' and sku=='LATTE' and when=='2026-09-22' else 60 if store=='S01' and sku=='LATTE' and when=='2026-09-23' else 0
                        rows['operations'].append([when,store,sku,600,unavailable,'演示：可售记录待业务核查' if unavailable else '正常营业（模拟）','项目生成的运营记录；不是库存推断'])
            if store=='S01':
                rows['sales'].append([f'REFUND-{week}',f'REFUND-{week}',end.isoformat(),store,'LATTE','经典拿铁' if week else '拿铁咖啡',-2 if week==1 else -1,26])
            elif week==1:
                rows['sales'].append(['REFUND-B', 'REFUND-B',end.isoformat(),store,'CAKE','芝士蛋糕',-1,22])
    blobs={}
    for kind,records in rows.items():
        out=io.StringIO();writer=csv.writer(out);writer.writerow(SCHEMAS[kind].values());writer.writerows(records)
        blobs[kind]=('\ufeff'+out.getvalue()).encode('utf-8')
    return blobs


def demo_task():
    return {'title':'两店经营周复盘 · 目标与实际（模拟）','question':'整体是否达标？哪些门店商品未达标，还缺哪些运营证据？',
            'start':'2026-09-21','end':'2026-09-27','previous_start':'2026-09-14','previous_end':'2026-09-20','metric':'amount',
            'confirmed_complete':True,'confirmation_note':'这是项目生成的模拟事件全集，已包含所选期间；不代表真实企业交接。','previous_review_id':None}
