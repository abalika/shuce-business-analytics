"""Portable application export of already-calculated immutable review snapshots."""
from decimal import Decimal
from io import BytesIO
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .engine import AnalysisError

STATES = {'met':'达标','shortfall':'未达标','missing_target':'未配置目标',
          'unknown_actual':'实际未知','unconfirmed_handover':'待完整交接确认',
          'zero_target':'零目标，比率不可计算','blocked':'关键问题待解决'}
KINDS = {'sales':'销售明细','targets':'经营目标','operations':'日运营记录'}
ILLEGAL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')
MONEY = '#,##0.00;[Red]-#,##0.00;0.00'


def value(cell, item, fmt=None):
    # Untrusted identifiers/notes must remain literal text, including leading '='.
    if isinstance(item, str):
        if len(item) > 32767:
            raise AnalysisError('有文字超过Excel单元格上限，请使用完整JSON导出。')
        cell.value = ILLEGAL.sub('�', item)
        cell.data_type = 's'
    else:
        cell.value = item
    if fmt:
        cell.number_format = fmt
    cell.alignment = Alignment(horizontal='left' if isinstance(item,str) else 'right',vertical='top',wrap_text=True,indent=1)
    cell.font = Font(name='Microsoft YaHei', size=11, color='233E38')


def amount(cents):
    return None if cents is None else float(Decimal(cents) / 100)


def row(sheet, items, formats=None, height=32):
    index = sheet.max_row + 1 if sheet.cell(1,1).value is not None else 1
    for col, item in enumerate(items, 1):
        value(sheet.cell(index,col), item, (formats or {}).get(col))
    sheet.row_dimensions[index].height = height
    return index


def banner(sheet, text, width):
    index = row(sheet, [text], height=38)
    sheet.merge_cells(start_row=index,start_column=1,end_row=index,end_column=width)
    cell = sheet.cell(index,1)
    cell.fill = PatternFill('solid', fgColor='163F35')
    cell.font = Font(name='Microsoft YaHei', size=15, bold=True, color='FFFFFF')


def note(sheet, label, text, width, height=36):
    # Long comments may exceed Excel's row-height limit. Continue in additional
    # rows rather than hide text below a fixed-height merged cell.
    capacity=max(30,int(sum(sheet.column_dimensions[get_column_letter(c)].width for c in range(2,width+1))*.8))
    parts=[];chunk='';line_width=0;lines=1
    for character in text:
        weight=2 if ord(character)>127 else 1
        if character=='\n' or line_width+weight>capacity:
            if lines>=16:
                parts.append((chunk,lines));chunk='';lines=1
            else:
                lines+=1
            line_width=0
        chunk+=character
        if character!='\n':line_width+=weight
    parts.append((chunk,lines))
    for n,(chunk,lines) in enumerate(parts):
        index = row(sheet,[label if n==0 else label+'（续）',chunk],height=max(height,lines*18+8))
        sheet.merge_cells(start_row=index,start_column=2,end_row=index,end_column=width)
        sheet.cell(index,1).font = Font(name='Microsoft YaHei', size=11, bold=True, color='163F35')


def table_header(sheet, headers):
    index = row(sheet, headers, height=42)
    for cell in sheet[index]:
        cell.fill = PatternFill('solid', fgColor='DFEBE3')
        cell.font = Font(name='Microsoft YaHei', size=11, bold=True, color='163F35')
        cell.alignment = Alignment(horizontal='center',vertical='center',wrap_text=True)
    return index


def setup(sheet, widths):
    sheet.sheet_view.showGridLines = False
    for col, width in enumerate(widths,1):
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0


def review_workbook(review, store='', sku=''):
    rows = [x for x in review['rows'] if (not store or x['store']==store) and (not sku or x['sku']==sku)]
    if not rows:
        raise AnalysisError('当前门店/SKU筛选没有匹配明细，请调整后导出。')
    wb = Workbook()
    wb.remove(wb.active)
    main = wb.create_sheet('经营复盘')
    context = wb.create_sheet('核查上下文')
    source = wb.create_sheet('来源与说明')
    currency = review['currency']
    scope = '全部组合' if not store and not sku else f"门店：{store or '全部'}，SKU：{sku or '全部'}"
    setup(main, [16,20,24,18,18,18,18,14,18,24,14])
    banner(main, review['task']['title'], 11)
    note(main, '本期 / 指标', f"{review['period']['start']} 至 {review['period']['end']}，{review['definition']['label']}（{currency}）", 11)
    note(main, '导出范围', f'以下概览为全任务范围；明细筛选为 {scope}。匹配{len(rows)} / 全部{len(review["rows"])}个组合，不受页面100行限制。', 11)
    totals = review['totals']
    row(main, ['全任务实际',amount(totals['actual_cents']),'全任务目标',amount(totals['target_cents']),
               '全任务达成率',None if totals['attainment'] is None else totals['attainment']/100,
               '达标/可评价',f"{totals['met_categories']} / {totals['evaluable_categories']}"], {2:MONEY,4:MONEY,6:'0.00%'})
    note(main, '口径', review['definition']['definition'], 11, 48)
    header = table_header(main, ['门店ID','商品SKU','商品名称',f'实际（{currency}）',f'目标（{currency}）',
                                f'实际−目标（{currency}）',f'目标缺口（{currency}）','达成率',f'基期变化（{currency}）','状态','证据编号'])
    for item in rows:
        row(main, [item['store'],item['sku'],item['name'],amount(item['actual_cents']),amount(item['target_cents']),
                   amount(item['difference_cents']),amount(item['shortfall_cents']),None if item['attainment'] is None else item['attainment']/100,
                   amount(item['period_change_cents']),STATES[item['status']],item['evidence_id']],
            {4:MONEY,5:MONEY,6:MONEY,7:MONEY,8:'0.00%',9:MONEY})
    main.auto_filter.ref = f'A{header}:K{main.max_row}'
    main.freeze_panes = f'D{header+1}'
    main.print_title_rows = f'1:{header}'
    note(main, '业务问题', review['task']['question'], 11, 50)
    note(main, '未知与零值', '未知数值留空，状态说明原因；真实零值保留0。金额不是支付到账或利润。',11)
    notes = review.get('report', {})
    for key, label in [('facts_note','已保存复盘说明'),('hypothesis','未验证原因假设'),('next_check','下一轮核查计划')]:
        text = notes.get(key) or '未保存；浏览器中的未提交草稿不包含在此文件。'
        note(main,label,text,11,42)

    setup(context, [16,20,18,18,18,18,18,18,16,16,18,18,18,18,55,14])
    banner(context,'金额构成与日运营核查',16)
    note(context,'范围 / 限制',f'{scope}；基期实际与目标差额含义不同。运营分钟仅汇总已提供记录，缺失日不补零，不推定经营原因。',16,48)
    note(context,'基期', f"{review['task'].get('previous_start')} 至 {review['task'].get('previous_end')}" if review['task'].get('previous_start') else '未配置；基期金额与变化留空。',16)
    header = table_header(context,['门店ID','商品SKU',f'基期实际（{currency}）',f'本期正向（{currency}）',f'本期负向（{currency}）',
                                   '基期正向数量','本期正向数量','本期负向数量','基期加权成交价','本期加权成交价',
                                   '运营记录天数','请求天数','已记录营业分钟','已记录不可售分钟','缺失运营日期','证据编号'])
    for item in rows:
        current, previous, operations = item['current'],item['previous'],item['operations']
        prior = bool(review['task'].get('previous_start'))
        row(context,[item['store'],item['sku'],amount(item['previous_cents']),amount(current['positive_cents']),amount(current['negative_cents']),
                     float(previous['positive_quantity']) if prior else None,float(current['positive_quantity']),float(current['negative_quantity']),
                     float(previous['weighted_positive_price']) if prior and previous['weighted_positive_price'] is not None else None,
                     float(current['weighted_positive_price']) if current['weighted_positive_price'] is not None else None,
                     operations['observed_days'],operations['calendar_days'],operations['open_minutes'],operations['unavailable_minutes'],
                     '、'.join(operations['missing_days']) or '无',item['evidence_id']],{3:MONEY,4:MONEY,5:MONEY,9:MONEY,10:MONEY},height=42)
    context.auto_filter.ref = f'A{header}:P{context.max_row}'
    context.freeze_panes = f'C{header+1}'
    context.print_title_rows = f'1:{header}'

    setup(source,[22,30,30,25,25,25,25,25])
    banner(source,'交接核对、来源与快照',8)
    note(source,'快照ID',review['id'],8)
    note(source,'生成时间',review['created_at'],8)
    note(source,'交接声明',review['task'].get('confirmation_note') or '未确认完整交接',8,48)
    ready = review['readiness']
    note(source,'可否评价','可评价，仍需阅读以下限制' if ready['can_evaluate'] else '暂不评价达成；有效记录统计仍可核对',8)
    row(source,['关联前净额',amount(ready['controls']['before_join_cents']),'对齐后净额',amount(ready['controls']['after_grain_alignment_cents'])],{2:MONEY,4:MONEY})
    seen_messages = set()
    for label, messages in [('关键问题',ready['blockers']),('警告',ready['warnings']),('说明',review['notices'])]:
        for message in messages:
            if message not in seen_messages:
                note(source,label,message,8,50)
                seen_messages.add(message)
    for kind, meta in review['sources'].items():
        banner(source,KINDS[kind]+'：'+meta['name'],8)
        note(source,'数据来源',meta['source'],8,60)
        note(source,'文件 / 批次',meta['filename']+' / '+meta['id'],8)
        note(source,'文件SHA256',meta['file_sha256'],8)
        note(source,'契约 / 单位',meta['contract_version']+' / '+meta['currency'],8)
        quality = meta['quality']
        row(source,['原始行',quality['input_rows'],'有效行',quality['valid_rows'],'隔离行',quality['rejected_rows'],'重复参与行',quality['duplicate_participants']])
        note(source,'唯一键',' / '.join(meta['quality']['key_fields']),8)
        for key, column in meta['mapping'].items():
            row(source,['字段映射',key,column])
    note(source,'使用说明','本文件为已计算快照；在Excel中修改不回写项目。原件、问题明细、逐条三源证据及AI摘要保留在项目和完整JSON内。',8,60)
    if review.get('followup'):
        note(source,'前次复盘',review['followup']['previous_review_id']+'；'+review['followup']['note'],8,48)
    source.freeze_panes='B2'
    for sheet in wb:
        sheet.print_options.horizontalCentered = True
        sheet.print_area = sheet.dimensions
    out=BytesIO()
    wb.save(out)
    return out.getvalue()
