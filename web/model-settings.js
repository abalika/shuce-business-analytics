/* Shared local settings UI. Secrets stay in the password field only until save/close. */
(()=>{
  const root=document.createElement('dialog');
  root.id='modelSettingsDialog';root.className='model-settings-dialog';
  root.setAttribute('aria-labelledby','modelSettingsTitle');
  root.innerHTML=`<form id="modelSettingsForm" autocomplete="off">
    <div class="card-head"><h2 id="modelSettingsTitle">模型设置</h2><button type="button" id="modelSettingsClose" class="link" aria-label="关闭模型设置">关闭</button></div>
    <p class="caption">填写兼容 Chat Completions 的 API 地址、模型 ID 和密钥。保存后立即生效，无需编辑文件或重启。</p>
    <p id="modelSettingsMode" class="caption"></p>
    <label>API 地址<input id="modelBaseUrl" type="url" maxlength="2048" placeholder="https://provider.example/v1" required autocomplete="off" spellcheck="false"></label>
    <label>模型 ID<input id="modelId" maxlength="200" placeholder="填写服务商提供的模型 ID" required autocomplete="off" spellcheck="false"></label>
    <label>API Key<input id="modelApiKey" type="password" maxlength="4096" placeholder="仅提交给本机后端" autocomplete="off" spellcheck="false" autocapitalize="none"></label>
    <p id="modelKeyHint" class="caption"></p>
    <label class="handover"><input id="modelRemember" type="checkbox">记住到本机（明文写入本项目 .env，请勿共享）</label>
    <p class="caption">默认仅本次运行生效，不写浏览器本地存储。此前已保存的本机配置不会因会话配置而删除；可用下方“清除配置”移除。普通 AI 使用会发送分析上下文，请先确认数据可传给所选服务。</p>
    <p class="caption">连接测试会发送一条不含业务数据的小请求，可能产生少量 API 费用；测试本身不保存配置。</p>
    <p id="modelSettingsMessage" role="status" aria-live="polite"></p>
    <div class="actions"><button type="button" id="modelTest" class="secondary">测试连接</button><button type="submit" id="modelSave" class="primary">保存并使用</button><button type="button" id="modelClear" class="link">清除配置</button></div>
  </form>`;
  document.body.appendChild(root);
  const find=id=>root.querySelector('#'+id);
  let revision=0,offline=true,pending=false,loaded=false;
  const secret=()=>{find('modelApiKey').value='';};
  const message=(text,error=false)=>{const node=find('modelSettingsMessage');node.textContent=text;node.classList.toggle('settings-error',error);};
  async function request(method,body){
    let result;
    try{result=await fetch('/api/model-settings'+(method==='TEST'?'/test':''),{method:method==='TEST'?'POST':method,cache:'no-store',
      headers:{'X-Shuce-Client':'1',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});}
    catch{throw Error('无法连接本地服务，请先启动数策，保留其他页面的草稿。');}
    let value;try{value=await result.json();}catch{throw Error('服务没有返回有效结果，请检查本地服务状态。');}
    if(!result.ok)throw Error(typeof value.detail==='string'?value.detail:'设置操作未完成，请重新打开后重试。');
    return value;
  }
  function controls(){
    for(const id of ['modelBaseUrl','modelId','modelApiKey','modelRemember','modelSave','modelTest','modelClear'])find(id).disabled=pending||offline||!loaded;
    find('modelSettingsClose').disabled=pending;
    for(const id of ['modelSave','modelTest','modelClear'])find(id).setAttribute('aria-busy',String(pending));
  }
  function apply(value){
    revision=value.revision;offline=value.offline;loaded=true;
    find('modelBaseUrl').value=value.base_url;find('modelId').value=value.model;
    find('modelRemember').checked=value.source==='local_file'&&value.has_key;
    find('modelKeyHint').textContent=value.has_key?'已配置密钥，界面不回显；地址不变时留空可继续使用。':'尚未配置密钥。请填写自己的 API Key，不要使用共享演示凭证。';
    find('modelSettingsMode').textContent=offline?'当前为离线演示，模型设置和连接测试已关闭。请先停止离线服务，再运行“启动数策.cmd”。':value.source==='session'?'当前配置仅本次运行使用。':value.source==='local_file'?'当前读取本机配置。':'支持兼容服务；密钥和模型来自你自己的服务商账户。';
    controls();
  }
  async function busy(action){
    if(pending||offline||!loaded)return;
    pending=true;controls();message('正在处理，请稍候…');
    try{await action();}catch(error){message(error.message,true);}finally{pending=false;controls();}
  }
  function payload(){
    return {base_url:find('modelBaseUrl').value,model:find('modelId').value,api_key:find('modelApiKey').value||null,remember:find('modelRemember').checked,revision};
  }
  async function refreshStatus(){
    const status=await fetch('/api/status',{cache:'no-store'}).then(r=>r.json());
    const node=document.querySelector('#aiStatus')||document.querySelector('#modelStatus');
    if(node)node.textContent=status.ai.configured?'模型已配置 · 可生成 AI 摘要':status.ai.offline?'离线 · 规则计算 · 非实时模型':'规则计算 · 模型未配置';
  }
  document.querySelector('#openModelSettings')?.addEventListener('click',async()=>{
    secret();loaded=false;pending=false;offline=true;controls();message('读取本机设置…');root.showModal();
    try{apply(await request('GET'));message('');if(!offline)find('modelBaseUrl').focus();}catch(error){message(error.message,true);}
  });
  find('modelSettingsClose').onclick=()=>{if(!pending)root.close();};
  root.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
  root.addEventListener('close',()=>{secret();find('modelRemember').checked=false;});
  find('modelSettingsForm').onsubmit=event=>{
    event.preventDefault();busy(async()=>{const saved=await request('POST',payload());secret();apply(saved);message(saved.detail);await refreshStatus();});
  };
  find('modelTest').onclick=()=>{if(find('modelSettingsForm').reportValidity())busy(async()=>message((await request('TEST',payload())).detail));};
  find('modelClear').onclick=()=>busy(async()=>{
    if(!window.confirm('清除本工作台保存的模型配置并改用规则计算？其他软件和外部网关不会被修改。')){message('已取消清除。');return;}
    const saved=await request('DELETE',{revision});secret();apply(saved);message(saved.detail);await refreshStatus();
  });
})();
