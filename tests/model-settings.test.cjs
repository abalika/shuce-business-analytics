const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const code=fs.readFileSync('web/model-settings.js','utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function ui({offline=false,configured=false,failure=false,waitForSave=null}={}){
  const fields=new Map(),requests=[];
  function element(id=''){
    return {id,value:'',checked:false,disabled:false,open:false,handlers:{},classList:{toggle(){}},
      setAttribute(){},focus(){},reportValidity(){return true;},
      addEventListener(name,fn){this.handlers[name]=fn;},
      showModal(){this.open=true;},close(){this.open=false;this.handlers.close?.();},
      querySelector(selector){return fields.get(selector.slice(1));},
      set innerHTML(value){this.html=value;for(const match of value.matchAll(/id="([^"]+)"/g))fields.set(match[1],element(match[1]));},
      get innerHTML(){return this.html;}};
  }
  const opener=element('openModelSettings'),status=element('aiStatus');let root,active=configured,revision=0;
  const snapshot=()=>({base_url:'https://provider.invalid/v1',model:'test-model',has_key:active,configured:active,offline,revision,source:'session'});
  const context=vm.createContext({document:{createElement(){root=element();return root;},body:{appendChild(){}},
    querySelector(selector){return selector==='#openModelSettings'?opener:selector==='#aiStatus'?status:null;}},
    window:{confirm:()=>true},
    get localStorage(){throw Error('Secrets must not access browser storage');},
    get sessionStorage(){throw Error('Secrets must not access browser storage');},
    fetch:async(url,options={})=>{
      requests.push({url,options});
      if(failure)throw Error('private-network-debug');
      if(url==='/api/status')return{json:async()=>({ai:{configured:active,offline}})};
      if(url.endsWith('/test'))return{ok:true,json:async()=>({ok:true,detail:'连接成功，未保存配置。'})};
      if(options.method==='POST'){
        if(waitForSave)await waitForSave;
        active=true;revision++;
        return{ok:true,json:async()=>({...snapshot(),detail:'已保存，立即生效。'})};
      }
      if(options.method==='DELETE'){active=false;revision++;}
      return{ok:true,json:async()=>({...snapshot(),detail:'已清除配置。'})};
    }});
  vm.runInContext(code,context);
  return{fields,requests,root,opener,status,async open(){await opener.handlers.click();},async save(){fields.get('modelSettingsForm').onsubmit({preventDefault(){}});await tick();}};
}

test('settings opens with blank password and an accessible labeled dialog',async()=>{
  const page=ui({configured:true});await page.open();
  assert.equal(page.fields.get('modelApiKey').value,'');
  assert.match(page.root.innerHTML,/type="password"/);
  assert.match(page.fields.get('modelKeyHint').textContent,/不回显/);
  assert.equal(page.fields.get('modelSave').disabled,false);
});
test('save sends to local backend, clears password and updates mode immediately',async()=>{
  const page=ui();await page.open();
  page.fields.get('modelApiKey').value='synthetic-ui-fixture';
  await page.save();
  const request=page.requests.find(r=>r.options.method==='POST');
  assert.equal(request.url,'/api/model-settings');
  assert.equal(JSON.parse(request.options.body).remember,false);
  assert.equal(JSON.parse(request.options.body).api_key,'synthetic-ui-fixture');
  assert.equal(page.fields.get('modelApiKey').value,'');
  assert.match(page.status.textContent,/模型已配置/);
});
test('closing the dialog always clears an unsaved password',async()=>{
  const page=ui();await page.open();page.fields.get('modelApiKey').value='not-retained';
  page.fields.get('modelSettingsClose').onclick();
  assert.equal(page.root.open,false);assert.equal(page.fields.get('modelApiKey').value,'');
});
test('offline mode disables controls and cannot start provider tests or saves',async()=>{
  const page=ui({offline:true});await page.open();
  assert.equal(page.fields.get('modelTest').disabled,true);
  assert.match(page.fields.get('modelSettingsMode').textContent,/离线/);
  page.fields.get('modelTest').onclick();await page.save();
  assert.equal(page.requests.filter(r=>r.options.method==='POST').length,0);
});
test('connection test leaves form credentials for save, but does not auto-save',async()=>{
  const page=ui();await page.open();page.fields.get('modelApiKey').value='test-only';
  page.fields.get('modelTest').onclick();await tick();
  assert.equal(page.requests.filter(r=>r.url.endsWith('/test')).length,1);
  assert.equal(page.requests.filter(r=>r.url==='/api/model-settings'&&r.options.method==='POST').length,0);
  assert.equal(page.fields.get('modelApiKey').value,'test-only');
  page.root.close();assert.equal(page.fields.get('modelApiKey').value,'');
});
test('repeated clicks do not submit concurrent saves',async()=>{
  let finish;const deferred=new Promise(resolve=>{finish=resolve;});
  const page=ui({waitForSave:deferred});await page.open();
  await page.save();await page.save();
  assert.equal(page.fields.get('modelSave').disabled,true);
  assert.equal(page.requests.filter(r=>r.options.method==='POST').length,1);
  finish();await tick();assert.equal(page.fields.get('modelSave').disabled,false);
});
test('connection failure exposes a recovery message, not raw network details',async()=>{
  const page=ui({failure:true});await page.open();
  assert.match(page.fields.get('modelSettingsMessage').textContent,/无法连接本地服务/);
  assert.doesNotMatch(page.fields.get('modelSettingsMessage').textContent,/private-network-debug/);
  assert.equal(page.fields.get('modelSave').disabled,true);
});
