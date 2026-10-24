/* Independent evidence inspector bound to the stored analysis, not the current chart. */
const evidenceState={analysis:null,id:null,offset:0,limit:25,total:0};
function evidenceButtons(ids){return ids.map(id=>`<button class="evidence-link" data-evidence="${escapeHtml(id)}">${escapeHtml(id)} ↗</button>`).join(' ');}
async function showEvidence(id,offset=0){
  if(!state.result)return;
  const analysis=state.result.id;
  evidenceState.analysis=analysis;evidenceState.id=id;evidenceState.offset=offset;
  $('#evidenceTitle').textContent='证据 '+id+' · 明细核查';
  $('#evidenceTable').textContent='正在读取证据…';
  if(!$('#evidenceDialog').open)$('#evidenceDialog').showModal();
  const r=await api(`/api/analyses/${analysis}/evidence/${id}?offset=${offset}&limit=${evidenceState.limit}`);
  if(evidenceState.analysis!==analysis||evidenceState.id!==id||evidenceState.offset!==offset)return;
  evidenceState.total=r.matched_rows;
  $('#evidenceContext').textContent=r.fact.label+'：'+value(r.fact.value,r.fact.unit,r.currency)+'。 '+r.definition.definition+' '+r.record_number_note;
  $('#evidenceTotals').innerHTML=Object.entries(r.period_totals).map(([period,total])=>`<span>${period==='previous'?'基期':'本期'}：${typeof total==='object'?`曝光 ${number(total.views)} / 点击 ${number(total.clicks)} / 点击率 ${value(total.ctr,'percent')}`:value(total,r.definition.unit,r.currency)}</span>`).join('');
  const orders=r.rows.some(row=>row.amount!==undefined);
  $('#evidenceTable').innerHTML=`<table><tr><th>记录序号</th><th>期间</th><th>日期</th><th>分类</th><th>编号</th>${orders?'<th>数量</th><th>单价</th><th>金额</th>':'<th>曝光</th><th>点击</th>'}</tr>${r.rows.map(row=>`<tr><td>${row.source_row}</td><td>${row.analysis_period==='previous'?'基期':'本期'}</td><td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.dimension)}</td><td>${escapeHtml(row.entity)||'—'}</td>${orders?`<td>${escapeHtml(row.quantity??'—')}</td><td>${escapeHtml(row.unit_price??'—')}</td><td>${value(row.amount/100,'currency',r.currency)}</td>`:`<td>${number(row.views)}</td><td>${number(row.clicks)}</td>`}</tr>`).join('')}</table>`;
  $('#evidencePage').textContent=`${offset+1}—${Math.min(offset+r.rows.length,r.matched_rows)} / ${number(r.matched_rows)} 条`;
  $('#evidencePrev').disabled=offset===0;$('#evidenceNext').disabled=offset+evidenceState.limit>=r.matched_rows;
  $('#evidenceDownload').href=`/api/analyses/${analysis}/evidence/${id}/export`;
}
$('#resultCard').addEventListener('click',event=>{const button=event.target.closest('[data-evidence]');if(button)showEvidence(button.dataset.evidence).catch(e=>error(e.message));});
$('#closeEvidence').onclick=()=>$('#evidenceDialog').close();
$('#evidencePrev').onclick=()=>showEvidence(evidenceState.id,Math.max(0,evidenceState.offset-evidenceState.limit)).catch(e=>error(e.message));
$('#evidenceNext').onclick=()=>showEvidence(evidenceState.id,evidenceState.offset+evidenceState.limit).catch(e=>error(e.message));
