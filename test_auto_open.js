const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const a={id:'a',codex_thread_id:'owner-a',auto_open_activity:true,gpt_collaboration:true},b={id:'b',codex_thread_id:'owner-b',auto_open_activity:true};
let resolvePost,calls=[],notices=[];
const ctx={autoOpenPending:false,online:true,snapshot:{service:{features:{panel_auto_open:true}}},conversationMeta:id=>id==='a'?a:b,
  renderSettings(){},refresh:async()=>{},toast:t=>notices.push(t),api:(path,body)=>{calls.push({path,body});return new Promise(resolve=>resolvePost=resolve);}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('async function setAutoOpenPreference'),source.indexOf('function renderSessions')),ctx);
(async()=>{
  const off=ctx.setAutoOpenPreference(a,false);assert.equal(calls[0].path,'/api/conversations/a/auto-open');assert.equal(calls[0].body.enabled,false);
  await ctx.setAutoOpenPreference(a,false);assert.equal(calls.length,1,'double click cannot send two writes');
  resolvePost({id:'a',enabled:false,updated_at:'now'});await off;
  assert.equal(a.auto_open_activity,false);assert.equal(b.auto_open_activity,true);assert.equal(a.gpt_collaboration,true);
  ctx.api=async()=>{throw new Error('offline');};await ctx.setAutoOpenPreference(a,true);assert.equal(a.auto_open_activity,false);assert.match(notices.at(-1),/未保存/);
  ctx.api=async()=>({id:'b',enabled:true});await ctx.setAutoOpenPreference(a,true);assert.equal(a.auto_open_activity,false,'mismatched response never writes either chat');assert.equal(b.auto_open_activity,true);
  ctx.api=async()=>({id:'a',enabled:true});ctx.refresh=async()=>{throw new Error('refresh failed');};await ctx.setAutoOpenPreference(a,true);assert.equal(a.auto_open_activity,true);assert.match(notices.at(-1),/设置已保存/,'successful persistence must not be reported as failed when refresh fails');
  ctx.online=false;ctx.api=()=>{throw new Error('should not write');};await ctx.setAutoOpenPreference(a,true);assert.equal(ctx.autoOpenPending,false);
  console.log('Auto-open checks passed: correct chat, independent collaboration, duplicate-write guard, honest failure, mismatched result rejected, offline no write.');
})().catch(error=>{console.error(error);process.exit(1);});
