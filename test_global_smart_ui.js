const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,{value:'text',innerHTML:'',dataset:{},setAttribute(){}});return nodes.get(id);};
const models=[{id:'argon-high',label:'Argon (High)'},{id:'flash-low',label:'Flash (Low)'},{id:'sonnet-medium',label:'Sonnet (Medium)'}];
const owner={id:'old-chat',fallback_preferences:{text:{enabled:false,models:['argon-high']}}};
const posted=[],notices=[];
const ctx={$,snapshot:{service:{features:{quota_failover:true,global_model_policy:true}},global_model_policy:{text:{enabled:true,models:['flash-low','argon-high']},image:{enabled:false,models:[]}},global_model_policy_version:5,health:{models},model_resolution:{text:{effective_model:'flash-low'}}},
  settingsOwner:()=>null,conversationMeta:()=>owner,online:true,uiSettings:{modelOrder:[]},orderedModels:(m,order=[])=>[...m].sort((a,b)=>(order.indexOf(a.id)<0?99:order.indexOf(a.id))-(order.indexOf(b.id)<0?99:order.indexOf(b.id))),
  modelFamilies:m=>m.map(x=>({key:x.id,label:x.label,variants:[x]})),modelEffortLabels:{},escape:String,render(){},refresh:async()=>{},toast:v=>notices.push(v),
  api:async(path,body)=>{posted.push({path,body});return {global_model_policy:{...ctx.snapshot.global_model_policy,[body.kind]:body.settings},global_model_policy_version:body.version+1};}};
vm.createContext(ctx);vm.runInContext(between('let fallbackExpanded','function installLightDismiss'),ctx);
vm.runInContext(between('function changeFallbackModel',"$('fallback-models').addEventListener('change'"),ctx);
(async()=>{
  vm.runInContext('fallbackExpanded=true',ctx);ctx.renderFallbackSettings();
  assert.equal($('fallback-scope-row').hidden,true,'global setup needs no target chat');
  assert.equal($('fallback-enabled').disabled,false);assert.match($('fallback-title').textContent,/全局智能选择/);
  assert.match($('global-smart-current').textContent,/Flash/);
  await ctx.moveFallbackModel('argon-high','up');
  assert.equal(posted[0].path,'/api/global-model-policy');assert.equal(posted[0].body.version,5);
  assert.deepEqual(Array.from(posted[0].body.settings.models),['argon-high','flash-low']);
  assert.deepEqual(owner.fallback_preferences.text.models,['argon-high'],'legacy window data is not rewritten');
  // Newly added rows live outside the selected-list container, but must use their own tier.
  const row={querySelectorAll:()=>[{dataset:{fallbackTier:'sonnet-medium'},value:'sonnet-medium'}]};
  await ctx.changeFallbackModel({target:{dataset:{fallbackFamily:'sonnet-medium'},checked:true,closest:()=>row}});
  assert.deepEqual(Array.from(posted.at(-1).body.settings.models),['argon-high','flash-low','sonnet-medium']);
  $('fallback-kind').value='image';ctx.renderFallbackSettings();
  assert.match($('fallback-models').innerHTML,/还没有候补/);
  await ctx.saveFallbackSettings({enabled:false,models:[]});
  assert.equal(posted.at(-1).body.kind,'image');assert.deepEqual(Array.from(ctx.snapshot.global_model_policy.image.models),[]);
  assert.equal(ctx.snapshot.global_model_policy.text.models.length,3,'image changes do not overwrite text candidates');
  const previous=ctx.snapshot.global_model_policy;ctx.api=async()=>{throw Error('conflict');};
  await ctx.saveFallbackSettings({enabled:true,models:['flash-low']});
  assert.equal(ctx.snapshot.global_model_policy,previous);assert.match(notices.at(-1),/conflict/);
  ctx.snapshot.global_model_policy={};ctx.settingsOwner=()=>owner;ctx.renderFallbackSettings();
  assert.match($('fallback-summary').textContent,/未配置/);assert.equal($('fallback-reset').textContent,'启用此名单');
  assert.equal(ctx.fallbackView().settings.enabled,true,'unconfigured global UI cannot borrow a window pause');
  assert.deepEqual(Array.from(ctx.fallbackCandidates(null,'image',{enabled:true})),[],'unverified text default cannot seed image list');
  ctx.online=false;ctx.renderFallbackSettings();assert.equal($('fallback-enabled').disabled,true);
  console.log('Global smart UI passed: owner-free global CAS, exact custom tiers/order, available-row add, text/image isolation, empty lists and conflict preservation.');
})().catch(e=>{console.error(e);process.exit(1);});
