const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
(async()=>{
  const nodes={'gpt-collaboration':{dataset:{},setAttribute(k,v){this[k]=v;}},'gpt-collaboration-label':{}};
  const chats=new Map([['a',{id:'a',codex_thread_id:'thread-a',gpt_collaboration:true}],['b',{id:'b',codex_thread_id:'thread-b',gpt_collaboration:true}]]);
  const calls=[],toasts=[];let resolvePost;
  const ctx={activeId:'a',online:true,collaborationPending:false,snapshot:{service:{features:{gpt_collaboration:true}}},$:id=>nodes[id],conversationMeta:id=>chats.get(id),
    api:(path,body)=>{calls.push({path,body});return new Promise(resolve=>resolvePost=resolve);},refresh:async()=>{},renderSettings:()=>{},toast:t=>toasts.push(t),renderSessions:()=>ctx.renderCollaborationControl(chats.get(ctx.activeId))};
  vm.createContext(ctx);vm.runInContext(between('function renderCollaborationControl','function renderSessions'),ctx);
  ctx.renderCollaborationControl(chats.get('a'));assert.equal(nodes['gpt-collaboration']['aria-checked'],'true');assert.equal(nodes['gpt-collaboration'].disabled,false);
  const off=ctx.toggleCollaboration();assert.equal(calls.length,1);assert.equal(calls[0].path,'/api/conversations/a/collaboration');assert.equal(calls[0].body.enabled,false);
  assert.equal(nodes['gpt-collaboration'].disabled,true);ctx.toggleCollaboration();assert.equal(calls.length,1);
  ctx.activeId='b';resolvePost({enabled:false});await off;assert.equal(chats.get('a').gpt_collaboration,false);assert.equal(chats.get('b').gpt_collaboration,true);
  assert.equal(nodes['gpt-collaboration']['aria-checked'],'true');
  ctx.activeId='a';ctx.renderSessions();assert.equal(nodes['gpt-collaboration']['aria-checked'],'false');assert.ok(nodes['gpt-collaboration-label'].textContent.endsWith('关'));
  const on=ctx.toggleCollaboration();assert.equal(calls[1].body.enabled,true);resolvePost({enabled:true});await on;assert.equal(chats.get('a').gpt_collaboration,true);
  ctx.online=false;ctx.renderSessions();assert.equal(nodes['gpt-collaboration'].disabled,true);await ctx.toggleCollaboration();assert.equal(calls.length,2);
  ctx.online=true;ctx.snapshot.service.features.gpt_collaboration=false;ctx.renderSessions();assert.equal(nodes['gpt-collaboration'].disabled,true);assert.ok(nodes['gpt-collaboration-label'].textContent.includes('待更新'));
  ctx.renderCollaborationControl({id:'standalone'});assert.equal(nodes['gpt-collaboration'].hidden,true);
  ctx.snapshot.service.features.gpt_collaboration=true;ctx.api=async()=>{throw new Error('write failed');};ctx.renderSessions();await ctx.toggleCollaboration();
  assert.equal(chats.get('a').gpt_collaboration,true);assert.equal(ctx.collaborationPending,false);assert.equal(toasts.at(-1),'write failed');
  console.log('Collaboration UI checks passed: correct chat target, persisted-state rendering, no duplicate writes, honest failure, manual controls kept separate.');
})().catch(e=>{console.error(e);process.exit(1)});
