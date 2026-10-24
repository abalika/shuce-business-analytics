/* Deterministic explanation from stored cents; editable report text is separate. */
function breakdownHtml(r,interactive=true){
  const b=r.change_breakdown;if(!b)return '';
  const money=c=>value(c/100,'currency',r.currency);
  return `<section class="amount-breakdown"><h3>这次金额变化由什么构成？</h3><div class="composition"><span>正向交易额变化 <b>${money(b.change.positive_cents)}</b></span><span>＋ 负向调整额变化 <b>${money(b.change.negative_cents)}</b></span><span>＝ 净交易额变化 <b>${money(b.change.net_cents)}</b></span></div><div class="table-wrap"><table><tr><th>分类 / 证据</th><th>正向金额变化</th><th>负向调整变化</th><th>正向数量<br>基期 → 本期</th><th>负向数量绝对值<br>基期 → 本期</th><th>加权成交单价<br>基期 → 本期</th></tr>${b.categories.map(c=>`<tr><td>${escapeHtml(c.dimension)} ${interactive?evidenceButtons([c.evidence_id]):''}</td><td>${money(c.change.positive_cents)}</td><td>${money(c.change.negative_cents)}</td><td>${number(c.previous.positive_quantity)} → ${number(c.current.positive_quantity)}</td><td>${number(c.previous.negative_quantity)} → ${number(c.current.negative_quantity)}</td><td>${c.previous.weighted_positive_price===null?'不可计算':money(Number(c.previous.weighted_positive_price)*100)} → ${c.current.weighted_positive_price===null?'不可计算':money(Number(c.current.weighted_positive_price)*100)}</td></tr>`).join('')}</table></div><p class="caption">展示 ${b.shown_categories} / ${b.all_categories} 个分类。${escapeHtml(b.note)}</p></section>`;
}

function calculatedNote(r){
  if(!r?.facts?.length)return '';
  const label=f=>`${f.label}：${value(f.value,f.unit,r.currency)}`;
  let text='系统填入的已计算事实（可编辑）：\n'+r.facts.slice(0,4).map(label).join('；')+'。';
  if(r.change_breakdown){const b=r.change_breakdown;text+='\n正向交易额变化'+value(b.change.positive_cents/100,'currency',r.currency)+'，负向调整额变化'+value(b.change.negative_cents/100,'currency',r.currency)+'。';for(const c of b.categories.slice(0,3))text+=`\n${c.dimension}：正向金额变化${value(c.change.positive_cents/100,'currency',r.currency)}，负向调整变化${value(c.change.negative_cents/100,'currency',r.currency)}。`;
  }
  return text+'\n以上说明数据中的变化构成；经营原因尚未验证。';
}

function composedNote(){
  const note=[['复盘说明',$('#reportNote').value],['原因假设（尚未验证）',$('#hypothesisNote').value],['下一步核查计划（不代表已执行）',$('#nextCheckNote').value]].filter(([,s])=>s.trim()).map(([title,s])=>title+'：\n'+s.trim()).join('\n\n');
  if(note.length>5000)throw new Error('三段复盘文字合计不能超过5000个字符，请精简后保存。');
  return note;
}

$('#fillCalculatedFacts').onclick=()=>{if(!state.result)return;const text=calculatedNote(state.result);if(!$('#reportNote').value.includes(text))$('#reportNote').value=[$('#reportNote').value.trim(),text].filter(Boolean).join('\n\n');$('#reportNote').focus();};
