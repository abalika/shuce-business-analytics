const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
for(const file of ['app','ops']){
  function load(fetch){
    const source=fs.readFileSync(`web/${file}.js`,'utf8').split('\n').find(line=>line.startsWith('async function api('));
    const context=vm.createContext({fetch,FormData});
    vm.runInContext(source,context);
    return context.api;
  }
  test(`${file}: disconnected server gives recovery instruction`,async()=>{
    const api=load(async()=>{throw Error('private-network-debug');});
    await assert.rejects(api('/status'),/无法连接本地服务.*保留草稿/);
  });
  test(`${file}: readable backend failure is retained`,async()=>{
    const api=load(async()=>({ok:false,status:503,json:async()=>({detail:'保留草稿，核查历史后再重试。'})}));
    await assert.rejects(api('/status'),/保留草稿，核查历史/);
  });
  test(`${file}: malformed response has readable fallback`,async()=>{
    const api=load(async()=>({ok:false,status:502,json:async()=>{throw Error('private-body');}}));
    await assert.rejects(api('/status'),/服务没有返回有效结果/);
  });
}
