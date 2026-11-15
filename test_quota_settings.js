const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const a={id:'a',fallback_preferences:{text:{enabled:true},image:{enabled:false,models:['flash-low']}}},b={id:'b',fallback_preferences:{}};
let owner=a,finish,fail,posted=[];const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'text',innerHTML:'',setAttribute(){},dataset:{}});return nodes.get(id);};
const ctx={snapshot:{model_order:[],model_order_version:0,service:{features:{quota_failover:true}},verified_image_models:['argon-high'],health:{models:[{id:'flash-low',label:'Flash (Low)'},{id:'argon-high',label:'Argon (High)'}]}},
  uiSettings:{modelOrder:['flash-low','argon-high']},uiSettingsKey:'prefs',localStorage:{setItem(){}},$:node,online:true,settingsOwner:()=>owner,render(){},toast(){},cleanModelOrder:v=>Array.isArray(v)?v:[],
  conversationMeta:id=>id==='a'?a:id==='b'?b:null,conversationPages:new Map(),pageCacheKey:id=>id,
  modelFamilies:models=>models.map(m=>({key:m.id,label:m.label,variants:[m]})),orderedModels:m=>m,escape:s=>String(s).replaceAll('<','&lt;'),modelEffortLabels:{},
  api:(path,body)=>{posted.push({path,body});return new Promise((yes,no)=>{finish=yes;fail=no;});},refresh:async()=>{}};
vm.createContext(ctx);vm.runInContext('let modelOrderSaving=false;'+between('async function syncBackendOrder','function moveModelOrder'),ctx);
vm.runInContext(between('let fallbackExpanded','function installLightDismiss'),ctx);
(async()=>{
  const migration=ctx.syncBackendOrder();assert.deepEqual(JSON.parse(JSON.stringify(posted[0].body)),{order:['flash-low','argon-high'],if_unset:true});
  finish({model_order:['argon-high','flash-low'],model_order_version:2});await migration;
  assert.equal(ctx.uiSettings.modelOrder[0],'argon-high','server CAS result wins concurrent migration');
  const before=posted.length;await ctx.syncBackendOrder();assert.equal(posted.length,before,'no redundant migration POST');
  const save=ctx.saveModelOrder(['flash-low']);assert.equal(posted.at(-1).body.version,2);fail(Error('conflict'));await save;
  assert.equal(ctx.uiSettings.modelOrder[0],'argon-high','failed save cannot overwrite order');
  node('fallback-kind').value='image';const pending=ctx.saveFallbackSettings({enabled:true,models:['flash-low']});
  owner=b;node('fallback-kind').value='text';finish({id:'a',fallback_preferences:{image:{enabled:true,models:['flash-low']}}});await pending;
  assert.equal(posted.at(-1).path,'/api/conversations/a/fallback');assert.equal(posted.at(-1).body.kind,'image');
  assert.equal(a.fallback_preferences.image.enabled,true);assert.equal(b.fallback_preferences.text,undefined);
  assert.deepEqual(Array.from(ctx.fallbackCandidates(a,'image',{enabled:true,models:[]})),[],'explicit empty list stays empty');
  assert.deepEqual(Array.from(ctx.fallbackCandidates(a,'image',{enabled:true})),['argon-high'],'image defaults only verified/selected candidates');
  const mismatch=ctx.saveFallbackSettings({enabled:true});finish({id:'wrong',fallback_preferences:{text:{enabled:true}}});await mismatch;
  assert.equal(b.fallback_preferences.text,undefined,'mismatched response cannot change another chat');
  owner=null;ctx.snapshot.conversations=[a,b];vm.runInContext('fallbackExpanded=true',ctx);ctx.renderFallbackSettings();
  assert.equal(node('fallback-expand').disabled,false,'overview can always open configuration');
  assert.equal(node('fallback-panel').hidden,false);assert.equal(node('fallback-scope-row').hidden,false);
  assert.match(node('fallback-summary').textContent,/按聊天配置/);assert.equal(node('fallback-enabled').disabled,true);assert.equal(node('fallback-models').innerHTML,'');
  vm.runInContext("fallbackScopeId='a'",ctx);ctx.renderFallbackSettings();
  assert.equal(node('fallback-enabled').disabled,false);assert.match(node('fallback-models').innerHTML,/data-fallback-family/);
  const rootSave=ctx.saveFallbackSettings({enabled:false,models:[]});assert.equal(posted.at(-1).path,'/api/conversations/a/fallback');
  finish({id:'a',fallback_preferences:{text:{enabled:false,models:[]}}});await rootSave;
  assert.equal(a.fallback_preferences.text.enabled,false);assert.equal(b.fallback_preferences.text,undefined);
  ctx.online=false;ctx.renderFallbackSettings();assert.equal(node('fallback-expand').disabled,false);assert.equal(node('fallback-enabled').disabled,true);
  assert.match(node('fallback-status').textContent,/仅查看/);const offlineCount=posted.length;await ctx.saveFallbackSettings({enabled:true});assert.equal(posted.length,offlineCount);
  owner=b;ctx.online=true;ctx.renderFallbackSettings();assert.equal(node('fallback-scope-row').hidden,true);assert.equal(ctx.fallbackView().owner,b,'fixed chat takes precedence over overview scope');
  console.log('Quota settings passed: CAS migration, failed saves, chat/kind isolation, empty and verified image candidates.');
})().catch(e=>{console.error(e);process.exit(1);});
