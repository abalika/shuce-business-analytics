"""Fixed three-table contracts and deterministic review, with explicit unknowns."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import pandas as pd
from .engine import AnalysisError

SCHEMAS = {
    'sales': {'event':'明细事件ID','order':'订单ID','date':'业务日期','store':'门店ID','sku':'商品SKU','name':'商品名称','quantity':'数量','price':'成交单价'},
    'targets': {'store':'门店ID','sku':'商品SKU','metric':'指标','currency':'单位','start':'开始日期','end':'结束日期','target':'目标值','basis':'目标依据'},
    'operations': {'date':'日期','store':'门店ID','sku':'商品SKU','open_minutes':'营业分钟','unavailable_minutes':'不可售分钟','reason':'原因','source_note':'来源备注'}
}
LABELS = {'amount':'净交易额','positive_amount':'正向交易额'}


def parse_date(v):
    text=str(v).strip()
    if text.endswith(' 00:00:00'):text=text[:-9]
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',text):raise ValueError('日期必须为YYYY-MM-DD业务日期')
    date.fromisoformat(text)
    return text


def number(v):
    n=Decimal(str(v).strip())
    if not n.is_finite():raise ValueError('数字须为有限值')
    return n


def mapping_for(raw,kind):
    return {key:(label if label in raw.columns else key if key in raw.columns else '') for key,label in SCHEMAS[kind].items()}


def normalize_source(raw,kind,mapping):
    if kind not in SCHEMAS:raise AnalysisError('未知复盘数据类型。')
    if not isinstance(mapping,dict) or any(not isinstance(v,str) for v in mapping.values()):raise AnalysisError('字段映射须为文本对象。')
    missing=[label for key,label in SCHEMAS[kind].items() if not mapping.get(key) or mapping[key] not in raw.columns]
    if missing:raise AnalysisError('请映射：'+'、'.join(missing))
    columns=[mapping[k] for k in SCHEMAS[kind]]
    if len(columns)!=len(set(columns)):raise AnalysisError('同一列不能映射到不同字段。')
    if len(raw)>(600000 if kind=='sales' else 20000):raise AnalysisError('辅助表最多20000行，销售最多600000行。')
    records=[];diagnostics=[]
    for index,row in enumerate(raw.fillna('').to_dict('records'),2):
        r={k:str(row[mapping[k]]).strip() for k in SCHEMAS[kind]};r['source_row']=index
        try:
            for key in ['store','sku']+(['event','order','name'] if kind=='sales' else []):
                if not r[key] or len(r[key])>120:raise ValueError(key+'须非空且不超过120字符')
            if kind=='sales':
                r['date']=parse_date(r['date']);q,p=number(r['quantity']),number(r['price'])
                if q==0 or p<=0 or q.copy_abs()>10**9 or p>10**9:raise ValueError('数量/单价超出支持范围或无效')
                cents=int((q*p*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                if abs(cents)>9*10**12:raise ValueError('单行金额过大')
                r.update(quantity=str(q),price=str(p),amount=cents,positive_amount=max(cents,0),negative_amount=min(cents,0))
            elif kind=='targets':
                r['start']=parse_date(r['start']);r['end']=parse_date(r['end'])
                if r['start']>r['end']:raise ValueError('目标期间倒置')
                r['metric']=next((k for k,v in LABELS.items() if r['metric'] in {k,v}),'')
                if not r['metric']:raise ValueError('仅支持净交易额或正向交易额目标')
                if r['currency'] not in {'元','GBP','USD','金额单位'}:raise ValueError('目标单位不支持')
                n=number(r['target'])
                if n<0 or n>10**12 or n!=n.quantize(Decimal('.01')):raise ValueError('目标须为非负金额且最多两位小数')
                if not r['basis'] or len(r['basis'])>600:raise ValueError('需要目标依据（最多600字符）')
                r['target_cents']=int(n*100)
            else:
                r['date']=parse_date(r['date']);a,b=number(r['open_minutes']),number(r['unavailable_minutes'])
                # Check the range before integer conversion: huge exponents must
                # be quarantined without allocating an enormous Python integer.
                if not 0<=b<=a<=1440 or a!=a.to_integral_value() or b!=b.to_integral_value():raise ValueError('营业/不可售分钟须为整数且0≤不可售≤营业≤1440')
                if len(r['reason'])>600 or len(r['source_note'])>600:raise ValueError('备注过长')
                r.update(open_minutes=int(a),unavailable_minutes=int(b))
            records.append(r)
        except (ValueError,InvalidOperation,OverflowError) as exc:
            diagnostics.append({'source_row':index,'kind':'quarantined','reason':str(exc),'original_values':str(row)})
    frame=pd.DataFrame(records,columns=list(SCHEMAS[kind])+['source_row']+({'sales':['amount','positive_amount','negative_amount'],'targets':['target_cents'],'operations':[]}[kind]))
    key={'sales':['store','event'],'targets':['store','sku','metric','start','end'],'operations':['date','store','sku']}[kind]
    duplicate=frame.duplicated(key,keep=False) if len(frame) else pd.Series([],dtype=bool)
    for r in frame[duplicate].to_dict('records'):
        diagnostics.append({'source_row':r['source_row'],'kind':'duplicate_key','reason':'唯一键重复，未自动删除','original_values':str(r)})
    if kind=='sales' and sum(abs(int(x)) for x in frame.amount)>10**14:raise AnalysisError('总金额超出精确分析范围。')
    if kind=='targets' and sum(int(x) for x in frame.target_cents)>10**14:raise AnalysisError('总目标金额过大。')
    quality={'input_rows':len(raw),'valid_rows':len(frame),'rejected_rows':sum(d['kind']=='quarantined' for d in diagnostics),'duplicate_participants':int(duplicate.sum()),'blocking':bool(diagnostics),'key_fields':key}
    return frame,quality,diagnostics


def money(group):
    positive=int(group.positive_amount.sum());negative=int(group.negative_amount.sum())
    positive_quantity = negative_quantity = Decimal(0)
    orders = set()
    for quantity, store, order, cents in zip(group.quantity, group.store, group.order, group.amount):
        quantity = Decimal(str(quantity))
        if quantity > 0:
            positive_quantity += quantity
        else:
            negative_quantity += quantity
        if cents > 0:
            orders.add((store, order))
    price = (Decimal(positive)/100/positive_quantity).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if positive_quantity else None
    return {'net_cents':positive+negative,'positive_cents':positive,'negative_cents':negative,
            'positive_quantity':str(positive_quantity),'negative_quantity':str(-negative_quantity),
            'weighted_positive_price':str(price) if price is not None else None,
            'orders':len(orders),'records':len(group)}


def build_review(sources,frames,task):
    try:
        start,end=parse_date(task['start']),parse_date(task['end'])
        if start>end or (date.fromisoformat(end)-date.fromisoformat(start)).days>=366:raise ValueError('复盘期间须为1至366天')
        ps,pe=task.get('previous_start'),task.get('previous_end')
        if bool(ps)!=bool(pe):raise ValueError('基期须同时提供起止日期')
        if ps:
            ps,pe=parse_date(ps),parse_date(pe)
            if ps>pe or not (pe<start or ps>end) or (date.fromisoformat(pe)-date.fromisoformat(ps)).days>=366:raise ValueError('基期无效、过长或与本期重叠')
    except (ValueError,KeyError) as exc:raise AnalysisError(str(exc)) from None
    metric=task.get('metric','amount')
    if metric not in LABELS:raise AnalysisError('仅支持净交易额/正向交易额复盘。')
    if task.get('confirmed_complete') and not task.get('confirmation_note','').strip():raise AnalysisError('确认完整交接时请说明依据。')
    s,t,o=frames['sales'],frames['targets'],frames['operations']
    current=s[(s.date>=start)&(s.date<=end)]
    previous=s[(s.date>=ps)&(s.date<=pe)] if ps else s.iloc[:0]
    blockers=[];warnings=[]
    for kind,meta in sources.items():
        if meta['quality']['blocking']:blockers.append(f"{meta['name']}有隔离记录或重复唯一键，请修订后重导。")
    relevant=t[(t.metric==metric)&(t.start<=end)&(t.end>=start)]
    if ((relevant.start!=start)|(relevant.end!=end)).any():blockers.append('目标期间与本期不一致，未擅自按天分摊。')
    if (relevant.currency!=sources['sales']['currency']).any():blockers.append('目标与销售单位不一致。')
    target_rows=relevant[(relevant.start==start)&(relevant.end==end)&(relevant.currency==sources['sales']['currency'])]
    # Every join occurs after grain alignment. Ambiguous target keys are never summed.
    target_map={}
    for key,g in target_rows.groupby(['store','sku']):
        if len(g)!=1:continue
        target_map[key]=g.iloc[0].to_dict()
    cg={k:g for k,g in current.groupby(['store','sku'])};pg={k:g for k,g in previous.groupby(['store','sku'])}
    keys=sorted(set(cg)|set(pg)|set(target_map))
    if len(keys)>20000:raise AnalysisError('当前复盘超过20000个门店/商品组合，请缩小范围。')
    days=[(date.fromisoformat(start)+timedelta(days=i)).isoformat() for i in range((date.fromisoformat(end)-date.fromisoformat(start)).days+1)]
    ops=o[(o.date>=start)&(o.date<=end)];og={k:g for k,g in ops.groupby(['store','sku'])}
    valid_sales=not sources['sales']['quality']['blocking']
    # A file may contain later cycles; future labels must not leak into this review.
    labels_by_key={key:g['name'].drop_duplicates().tolist() for key,g in s[s.date<=end].sort_values('date').groupby(['store','sku'])}
    rows=[]
    no_sales = s.iloc[:0]
    no_operations = o.iloc[:0]
    for key in keys:
        g=cg.get(key,no_sales);prior=pg.get(key,no_sales);total=money(g);old=money(prior)
        actual=(total['net_cents'] if metric=='amount' else total['positive_cents']) if len(g) or (task.get('confirmed_complete') and valid_sales) else None
        prior_value=(old['net_cents'] if metric=='amount' else old['positive_cents']) if ps and len(prior) else None
        target=target_map.get(key);target_cents=int(target['target_cents']) if target else None
        change=actual-target_cents if actual is not None and target_cents is not None and not blockers and task.get('confirmed_complete') else None
        state='blocked' if blockers else 'missing_target' if target is None else 'unknown_actual' if actual is None else 'unconfirmed_handover' if not task.get('confirmed_complete') else 'zero_target' if target_cents==0 else 'met' if change>=0 else 'shortfall'
        operations=og.get(key,no_operations);observed=set(operations.date)
        missing=[d for d in days if d not in observed]
        open_days = set(operations.loc[operations.open_minutes > 0, 'date'])
        sale_days = set(g.loc[g.amount > 0, 'date'])
        closed_conflicts = sorted((observed - open_days) & sale_days)
        if closed_conflicts:warnings.append(f'{key[0]}/{key[1]}闭店日有正向交易，需核查业务日期。')
        labels=labels_by_key.get(key,[])
        rows.append({'store':key[0],'sku':key[1],'name':labels[-1] if labels else '无交易名称','names':labels,
                     'actual_cents':actual,'target_cents':target_cents,'difference_cents':change,'shortfall_cents':max(-change,0) if change is not None else None,
                     'attainment':round(actual/target_cents*100,6) if actual is not None and target_cents and not blockers and task.get('confirmed_complete') else None,'status':state,
                     'previous_cents':prior_value,'period_change_cents':actual-prior_value if actual is not None and prior_value is not None else None,
                     'current':total,'previous':old,'target_row':int(target['source_row']) if target else None,'target_basis':target['basis'] if target else '',
                     'operations':{'observed_days':len(observed),'calendar_days':len(days),'missing_days':missing,'open_minutes':int(operations.open_minutes.sum()),'unavailable_minutes':int(operations.unavailable_minutes.sum()),'closed_sale_dates':closed_conflicts}})
    # Total is from the original selected sales, not a repeated target/operations join.
    total=money(current);selected_amount=total['net_cents'] if metric=='amount' else total['positive_cents']
    reconciled=sum(r['current']['net_cents'] for r in rows)==total['net_cents']
    assert reconciled
    covered=bool(rows) and all(r['target_cents'] is not None and r['actual_cents'] is not None for r in rows) and not blockers and bool(task.get('confirmed_complete'))
    target_total=sum(r['target_cents'] for r in rows) if covered else None
    if not covered:warnings.append('目标或实际评价范围不完整，整体目标/达成不可计算；不把缺失当零。')
    if not task.get('confirmed_complete'):warnings.append('未声明所选范围完整交接，已有金额仅为已提供记录统计，不据此判断目标达成。')
    if any(r['operations']['missing_days'] for r in rows):warnings.append('部分运营日期缺失，不可售记录只能作已知线索，缺失日保留未知。')
    if ps and len(days)!=(date.fromisoformat(pe)-date.fromisoformat(ps)).days+1:warnings.append('本期与基期天数不同，不直接解读总量为同等经营效果。')
    rows.sort(key=lambda r:(r['shortfall_cents'] is None,-(r['shortfall_cents'] or 0),r['store'],r['sku']))
    facts=[{'id':'E1','label':'本期有效记录'+LABELS[metric],'value':selected_amount/100,'unit':'currency'}]
    if covered:
        facts.extend([{'id':'E2','label':'同范围计划目标','value':target_total/100,'unit':'currency'},{'id':'E3','label':'实际减目标','value':(selected_amount-target_total)/100,'unit':'currency'},{'id':'E4','label':'整体目标达成率','value':round(selected_amount/target_total*100,6) if target_total else None,'unit':'percent'}])
    for index,r in enumerate(rows):
        r['evidence_id']=f'E{len(facts)+1}'
        facts.append({'id':r['evidence_id'],'label':f"{r['store']}/{r['sku']} {r['name']}实际减目标",'value':r['difference_cents']/100 if r['difference_cents'] is not None and not blockers else None,'unit':'currency'})
    evaluable=[r for r in rows if r['status'] in {'met','shortfall'}]
    task = {**task, 'start':start, 'end':end, 'previous_start':ps or None, 'previous_end':pe or None}
    return {'operation':'compare','metric':metric,'definition':{'label':LABELS[metric],'definition':'商品交易逐行数量×成交单价取分；目标来自提供的同周期同门店SKU计划，实际减目标不是经营原因。','unit':'currency'},
            'period':{'start':start,'end':end},'previous_period':{'start':ps,'end':pe} if ps else None,'task':task,'sources':sources,'rows':rows,'facts':facts,
            'totals':{**total,'actual_cents':selected_amount,'target_cents':target_total,'difference_cents':selected_amount-target_total if covered else None,'attainment':round(selected_amount/target_total*100,6) if target_total else None,'evaluable_categories':len(evaluable),'met_categories':sum(r['status']=='met' for r in evaluable),'categories':len(rows)},
            'readiness':{'can_evaluate':not blockers and bool(task.get('confirmed_complete')),'overall_target_complete':covered,'blockers':blockers,'warnings':list(dict.fromkeys(warnings)),'controls':{'before_join_cents':total['net_cents'],'after_grain_alignment_cents':sum(r['current']['net_cents'] for r in rows),'reconciled':reconciled}},
            'currency':sources['sales']['currency'],'notices':['目标为提供的计划，不是预测。不可售记录与指标同屏不证明因果，也不能据此估算损失。','完整交接确认是人工声明，不等于机器证明业务真实性。']+list(dict.fromkeys(warnings)),'source':'；'.join(m['source'] for m in sources.values())}
