'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const functions=source.slice(source.indexOf('function armAccountReturnSync()'),source.indexOf('function toast('));
function fixture(){
  const calls=[],notices=[];
  const ctx={accountReturnSync:{armed:false},document:{hidden:false},online:true,
    snapshot:{service:{features:{account_sync:true},active_tasks:0,checking_processes:0},health:{checking:false,account:{state:'known'}}},
    startPolling:()=>calls.push('poll'),api:async path=>calls.push(path),toast:text=>notices.push(text),renderAccount(){}};
  vm.createContext(ctx);vm.runInContext(functions,ctx);
  return {ctx,calls,notices};
}
(async()=>{
  const {ctx,calls}=fixture();ctx.armAccountReturnSync();ctx.accountReturnSync.opening=false;
  ctx.accountReturnFocused();await ctx.syncReturnedAccount();assert.equal(calls.length,0,'no synchronization without leaving first');
  ctx.accountReturnDeparted();ctx.document.hidden=true;ctx.accountReturnFocused();assert.equal(calls.length,0);
  ctx.document.hidden=false;ctx.accountReturnFocused();assert.deepEqual(calls,['poll']);
  ctx.snapshot.service.active_tasks=1;await ctx.syncReturnedAccount();assert.equal(calls.length,1,'busy tasks defer synchronization');assert.equal(ctx.accountReturnSync.armed,true);
  ctx.snapshot.service.active_tasks=0;let resolve;
  ctx.api=path=>{calls.push(path);return new Promise(r=>resolve=r);};
  const pending=ctx.syncReturnedAccount();await ctx.syncReturnedAccount();assert.equal(calls.filter(x=>x==='/api/account/sync').length,1,'focus events cannot duplicate an in-flight request');
  resolve({accepted:true});await pending;ctx.accountReturnFocused();await ctx.syncReturnedAccount();assert.equal(calls.filter(x=>x==='/api/account/sync').length,1,'one return causes one sync');
  const busy=fixture();busy.ctx.armAccountReturnSync();Object.assign(busy.ctx.accountReturnSync,{ready:true,opening:false});
  busy.ctx.api=async()=>{throw Object.assign(new Error('busy'),{status:409});};await busy.ctx.syncReturnedAccount();assert.equal(busy.ctx.accountReturnSync.armed,true,'definite busy rejection may defer to next existing poll');
  const unknown=fixture();unknown.ctx.armAccountReturnSync();Object.assign(unknown.ctx.accountReturnSync,{ready:true,opening:false});let attempts=0;
  unknown.ctx.api=async()=>{attempts++;throw new Error('unknown delivery');};await unknown.ctx.syncReturnedAccount();await unknown.ctx.syncReturnedAccount();assert.equal(attempts,1,'unknown delivery must not be retried');
  const opening=fixture();opening.ctx.armAccountReturnSync();opening.ctx.accountReturnSync.ready=true;await opening.ctx.syncReturnedAccount();assert.equal(opening.calls.length,0,'launcher response must be confirmed first');
  const old=fixture();old.ctx.snapshot.service.features.account_sync=false;old.ctx.armAccountReturnSync();Object.assign(old.ctx.accountReturnSync,{ready:true,opening:false});await old.ctx.syncReturnedAccount();assert.equal(old.calls.length,0,'old backend is never mistaken for synchronized models');
  const actions=fs.readFileSync(__dirname+'/panel_actions.js','utf8'),nodes=new Map();
  const element=id=>{if(!nodes.has(id))nodes.set(id,{checked:false,disabled:false,hidden:false,textContent:'',close(){}});return nodes.get(id);};
  const race=fixture();Object.assign(race.ctx,{$:element,panelAction:{mode:'account'},panelActionPending:false,renderSettings(){}});
  vm.runInContext(actions.slice(actions.indexOf('async function submitPanelAction('),actions.indexOf('function openFulltext()')),race.ctx);
  let opened;race.ctx.api=()=>new Promise(resolve=>opened=resolve);
  const launch=race.ctx.submitPanelAction({preventDefault(){}});
  assert.equal(race.ctx.accountReturnSync.opening,true);
  race.ctx.accountReturnDeparted();race.ctx.accountReturnFocused();assert.equal(race.ctx.accountReturnSync.ready,true);
  opened({opened:true});await launch;assert.equal(race.ctx.accountReturnSync.ready,true,'returning before launch response does not lose return state');assert.equal(race.ctx.accountReturnSync.opening,false);
  const host=fixture(),click={isTrusted:true,type:'pointerdown',button:0,target:{closest:()=>null}};
  host.ctx.accountReturnInteracted(click);assert.equal(host.calls.length,0,'initial launch button event occurs before arming');
  host.ctx.armAccountReturnSync();host.ctx.accountReturnInteracted(click);assert.equal(host.calls.length,0,'opening response must complete first');
  host.ctx.accountReturnSync.opening=false;
  host.ctx.accountReturnInteracted({...click,isTrusted:false});assert.equal(host.calls.length,0,'synthetic events cannot trigger return sync');
  host.ctx.accountReturnInteracted({...click,button:2});assert.equal(host.calls.length,0,'context menu interaction does not trigger sync');
  host.ctx.document.hidden=true;host.ctx.accountReturnInteracted(click);assert.equal(host.calls.length,0,'hidden pages do not trigger sync');host.ctx.document.hidden=false;
  host.ctx.accountReturnInteracted({...click,target:{closest:()=>({})}});assert.equal(host.calls.length,0,'launch/confirmation/manual-sync/composer controls retain their own action');
  host.ctx.accountReturnInteracted(click);host.ctx.accountReturnInteracted(click);assert.deepEqual(host.calls,['poll'],'host without blur/focus resumes once on a trusted panel click');
  await host.ctx.syncReturnedAccount();host.ctx.accountReturnInteracted(click);assert.deepEqual(host.calls,['poll','/api/account/sync'],'repeated clicks after sync do not launch another request');
  const keyboard=fixture();keyboard.ctx.armAccountReturnSync();keyboard.ctx.accountReturnSync.opening=false;
  keyboard.ctx.accountReturnInteracted({...click,type:'keydown'});assert.deepEqual(keyboard.calls,['poll'],'trusted non-composer keyboard interaction also restores return detection');
  const pendingHost=fixture();pendingHost.ctx.armAccountReturnSync();Object.assign(pendingHost.ctx.accountReturnSync,{opening:false,pending:true});pendingHost.ctx.accountReturnInteracted(click);assert.equal(pendingHost.calls.length,0,'pending synchronization is never overwritten');
  console.log('Account return sync passed: explicit launch, departure/return, busy defer, one request, no ambiguous retry, feature gate.');
})().catch(error=>{console.error(error);process.exit(1);});
