const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const policy=(thread,enabled=true,agy=true,muse=false,claude=false)=>({schema_version:1,codex_thread_id:thread,enabled,providers:{agy:{enabled:agy},muse:{enabled:muse},claude:{enabled:claude}}});
(async()=>{
  const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},setAttribute(k,v){this[k]=v;}});return nodes.get(id);};
  const chats=new Map([['a',{id:'a',title:'A',codex_thread_id:'thread-a',gpt_collaboration:true,multi_ai_collaboration:policy('thread-a')}],['b',{id:'b',title:'B',codex_thread_id:'thread-b',gpt_collaboration:true,multi_ai_collaboration:policy('thread-b')}] ]);
  const calls=[],toasts=[];let resolvePost;
  const ctx={activeId:'a',online:true,collaborationPending:false,multiAIExpanded:false,snapshot:{service:{features:{multi_ai_collaboration:true}}},$:node,conversationMeta:id=>chats.get(id),
    api:(path,body)=>{calls.push({path,body});return new Promise(resolve=>resolvePost=resolve);},refresh:async()=>{},renderSettings:()=>{},toast:t=>toasts.push(t),renderSessions:()=>ctx.renderCollaborationControl(chats.get(ctx.activeId))};
  vm.createContext(ctx);vm.runInContext(between('function multiAIState','async function setAutoOpenPreference'),ctx);
  ctx.renderMultiAISettings(chats.get('a'));assert.equal(node('settings-collaboration')['aria-checked'],'true');assert.equal(node('settings-collaboration').disabled,false);
  assert.equal(node('provider-muse')['aria-checked'],'false');assert.equal(node('provider-claude')['aria-checked'],'false');
  const off=ctx.setCollaborationPreference(chats.get('a'),false);assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/conversations/a/multi-ai-collaboration');assert.equal(calls[0].body.enabled,false);assert.equal(calls[0].body.codex_thread_id,'thread-a');
  await ctx.setMultiAIPreference(chats.get('a'),{providers:{muse:{enabled:true}}});assert.equal(calls.length,1,'pending writes cannot be duplicated');
  ctx.activeId='b';resolvePost(policy('thread-a',false));await off;
  assert.equal(chats.get('a').multi_ai_collaboration.enabled,false);assert.equal(chats.get('b').multi_ai_collaboration.enabled,true);assert.equal(node('gpt-collaboration').dataset.enabled,'true','late response renders current chat');
  const provider=ctx.setMultiAIPreference(chats.get('a'),{providers:{muse:{enabled:true}}});
  assert.equal(calls[1].body.enabled,undefined,'provider update never reopens total');assert.equal(calls[1].body.providers.muse.enabled,true);
  resolvePost(policy('thread-a',false,true,true));await provider;assert.equal(chats.get('a').multi_ai_collaboration.enabled,false);assert.equal(chats.get('a').gpt_collaboration,false);
  ctx.renderMultiAISettings(chats.get('a'));assert.equal(node('provider-muse')['aria-checked'],'true');assert.equal(node('settings-collaboration')['aria-checked'],'false');assert.match(node('multi-ai-note').textContent,/总协作已暂停/);
  const on=ctx.setCollaborationPreference(chats.get('a'),true);resolvePost(policy('thread-a',true,false,true));await on;
  assert.equal(chats.get('a').multi_ai_collaboration.providers.agy.enabled,false);assert.equal(chats.get('a').gpt_collaboration,false,'effective legacy Agy alias remains paused');assert.equal(chats.get('a').multi_ai_collaboration.providers.muse.enabled,true);
  const previous=JSON.stringify(chats.get('a'));
  const mismatch=ctx.setCollaborationPreference(chats.get('a'),false);resolvePost(policy('thread-b',false));await mismatch;
  assert.equal(JSON.stringify(chats.get('a')),previous,'mismatching response must not change source chat');assert.match(toasts.at(-1),/不匹配/);
  ctx.api=async()=>{throw new Error('write failed');};await ctx.setCollaborationPreference(chats.get('a'),false);assert.equal(JSON.stringify(chats.get('a')),previous);assert.equal(ctx.collaborationPending,false);assert.match(toasts.at(-1),/未保存.*write failed/);
  const count=calls.length;ctx.online=false;await ctx.setCollaborationPreference(chats.get('a'),false);assert.equal(calls.length,count);ctx.renderMultiAISettings(chats.get('a'));assert.equal(node('settings-collaboration').disabled,true);
  ctx.online=true;ctx.snapshot.service.features.multi_ai_collaboration=false;ctx.renderMultiAISettings(chats.get('a'));assert.equal(node('provider-agy').disabled,true);assert.equal(node('settings-collaboration').textContent,'待更新');
  ctx.snapshot.service.features.multi_ai_collaboration=true;ctx.renderMultiAISettings({id:'root'});assert.equal(node('settings-collaboration').disabled,true);assert.equal(node('provider-muse').disabled,true);
  const malformed={...chats.get('a'),multi_ai_collaboration:{...policy('thread-a'),enabled:'true'}};assert.equal(ctx.multiAIState(malformed),null);ctx.renderMultiAISettings(malformed);assert.equal(node('settings-collaboration').disabled,true);
  for(const malformedPolicy of [
    {...policy('thread-a'),unexpected:true},
    {...policy('thread-a'),providers:{...policy('thread-a').providers,other:{enabled:true}}},
    {...policy('thread-a'),providers:{...policy('thread-a').providers,agy:{enabled:true,unexpected:true}}}
  ])assert.equal(ctx.multiAIState({...chats.get('a'),multi_ai_collaboration:malformedPolicy}),null,'unknown canonical keys fail closed');
  let opened=0;ctx.openSettings=()=>opened++;ctx.openMultiAISettings();assert.equal(opened,1);assert.equal(ctx.multiAIExpanded,true,'toolbar opens details instead of toggling');
  assert.equal(calls.length,count,'rendering/opening preferences invokes no extra endpoint');
  console.log('Multi-AI UI checks passed: exact chat patch, total/provider independence, preserved pause, late/malformed/error response protection, root/old-server disabled and no dispatch side effect.');
})().catch(e=>{console.error(e);process.exit(1)});
