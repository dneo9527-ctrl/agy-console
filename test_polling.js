// Exercise the actual browser scheduling functions with deterministic visibility/timers.
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const events={},timers=new Map();let next=0,stateCalls=0,activityCalls=0,memoryCalls=0;
  const ctx={uiSettings:{idleMs:12000},online:true,snapshot:{service:{active_tasks:0,features:{resources:true}},health:{}},readMemorySummary:async()=>{memoryCalls++;},submitting:false,reloadBusy:false,idlePolls:0,stashComposerDraft:()=>{},composerTurn:()=>null,composerConversation:()=>null,viewMode:'panel',pollEpoch:0,pollTimers:new Map(),document:{hidden:false,addEventListener:(name,fn)=>events[name]=fn},window:{addEventListener:()=>{}},
    setTimeout:(fn,delay)=>{timers.set(++next,{fn,delay});return next;},clearTimeout:id=>timers.delete(id),
    refresh:async()=>{stateCalls++;},refreshActivity:async()=>{activityCalls++;},active:()=>null,isBusy:()=>false};
  vm.createContext(ctx);vm.runInContext(between('function stopPolling()','if([\'all\','),ctx);
  ctx.startPolling();await flush();assert.equal(stateCalls,1);assert.equal(activityCalls,1);assert.equal(memoryCalls,1);assert.equal(timers.size,3);
  assert.ok([...timers.values()].some(timer=>timer.delay===4000));assert.ok([...timers.values()].some(timer=>timer.delay===30000));
  assert.ok([...timers.values()].some(timer=>timer.delay===20000));
  const fire=async delay=>{const [id,timer]=[...timers].find(([,value])=>value.delay===delay);timers.delete(id);await timer.fn();await flush();};
  await fire(4000);assert.ok([...timers.values()].some(timer=>timer.delay===8000));
  await fire(8000);assert.ok([...timers.values()].some(timer=>timer.delay===12000));
  ctx.snapshot.service.active_tasks=1;ctx.memoryPollSync();await flush();assert.equal(memoryCalls,2);
  assert.ok([...timers.values()].some(timer=>timer.delay===5000),'another window makes sampling busy immediately');
  assert.ok(![...timers.values()].some(timer=>timer.delay===20000));
  ctx.snapshot.service.active_tasks=0;ctx.memoryPollSync();await flush();assert.equal(memoryCalls,3,'busy to idle samples the post-task baseline');
  assert.ok([...timers.values()].some(timer=>timer.delay===20000));
  ctx.document.hidden=true;events.visibilitychange();assert.equal(timers.size,0);
  await flush();assert.equal(stateCalls,3);assert.equal(activityCalls,1);
  ctx.document.hidden=false;events.visibilitychange();await flush();assert.equal(stateCalls,4);assert.equal(activityCalls,2);assert.equal(timers.size,3);
  ctx.viewMode='activity';ctx.startPolling();await flush();assert.equal(timers.size,3);assert.ok([...timers.values()].some(timer=>timer.delay===2400));
  ctx.snapshot.service.active_tasks=1;ctx.composerTurn=()=>({started_at:new Date().toISOString()});ctx.$=()=>({textContent:''});ctx.duration=String;
  ctx.startPolling();await flush();assert.equal(timers.size,4);assert.ok([...timers.values()].some(timer=>timer.delay===1600));assert.ok([...timers.values()].some(timer=>timer.delay===1000));
  ctx.stopPolling();assert.equal(timers.size,0);
  ctx.snapshot.service.active_tasks=0;ctx.composerTurn=()=>null;ctx.uiSettings.idleMs=4000;
  ctx.startPolling();await flush();await fire(4000);assert.ok([...timers.values()].some(timer=>timer.delay===4000));ctx.stopPolling();
  let finishMemory;ctx.readMemorySummary=()=>{memoryCalls++;return new Promise(resolve=>finishMemory=resolve);};
  ctx.startPolling();await flush();const beforeTransition=memoryCalls;
  ctx.snapshot.service.active_tasks=1;ctx.memoryPollSync();ctx.snapshot.service.active_tasks=0;ctx.memoryPollSync();
  assert.equal(memoryCalls,beforeTransition,'busy changes do not overlap a pending sample');
  finishMemory();await flush();assert.ok([...timers.values()].some(timer=>timer.delay===0));
  ctx.document.hidden=true;events.visibilitychange();assert.equal(timers.size,0,'hidden also cancels deferred resample');
  const deferred=[];let requests=0;
  const state={lastServiceInstance:null,restoreFirstDraft:()=>{},stateRefreshPromise:null,activeId:null,requestedCodexThread:null,boundViewApplied:false,kindRestored:true,online:false,synchronizeDetail:()=>{},followBoundActivity:()=>false,
    api:()=>{requests++;return new Promise(resolve=>deferred.push(resolve));},render:()=>{},active:()=>null};
  vm.createContext(state);vm.runInContext(between('function refresh(afterChange=true)','async function newConversation'),state);
  const one=state.refresh(false),two=state.refresh(false),fresh=state.refresh(true);
  assert.equal(requests,1);deferred.shift()({conversations:[],health:{}});await one;await two;await flush();assert.equal(requests,2);
  deferred.shift()({conversations:[],health:{}});await fresh;assert.equal(state.stateRefreshPromise,null);
  const ownerChat={id:'own',codex_thread_id:'thread-a'},otherChat={id:'other',codex_thread_id:'thread-b'};
  let owner='thread-a',opened=null,posts=0;
  const routing={viewMode:'activity',snapshot:{conversations:[ownerChat,otherChat]},sessionStorage:{getItem:()=>owner},active:()=>otherChat,
    api:async()=>{posts++;throw new Error('Unexpected new conversation');},selectConversation:id=>opened=id,$:()=>({value:'',focus(){}}),render:()=>{},toast:message=>{throw new Error(message);}};
  vm.createContext(routing);vm.runInContext(between('function fixedOwnerConversation()','function historySettings('),routing);
  assert.equal(routing.composerConversation().id,'own');routing.viewMode='external';assert.equal(routing.composerConversation().id,'own');
  routing.viewMode='panel';assert.equal(routing.composerConversation().id,'other');
  vm.runInContext(between('async function newConversation()',"$('new-task').addEventListener"),routing);
  await routing.newConversation();assert.equal(opened,'own');assert.equal(posts,0);assert.equal(routing.snapshot.conversations.length,2);
  owner=null;routing.viewMode='activity';assert.equal(routing.composerConversation(),null);
  let recoveryRenders=0;state.api=async()=>null;state.online=false;state.snapshot={preserved:true};state.render=()=>recoveryRenders++;
  await state.refresh(false);assert.equal(state.online,true);assert.equal(state.snapshot.preserved,true);assert.equal(recoveryRenders,1);
  let updates=0;state.loadUpdatedPage=async()=>updates++;
  state.api=async()=>({conversations:[],health:{},service:{pid:1}});await state.refresh(false);assert.equal(state.lastServiceInstance,'pid:1');
  state.api=async()=>({conversations:[],health:{},service:{pid:2,instance_id:'first-modern'}});await state.refresh(false);assert.equal(updates,1);
  await state.refresh(false);assert.equal(updates,1);
  state.api=async()=>({conversations:[],health:{},service:{pid:2,instance_id:'same-pid-reload'}});await state.refresh(false);assert.equal(updates,2);
  console.log('Polling checks passed: hidden pause, resume sync, bounded timers, no overlapping state requests, post-action fresh read.');
})().catch(error=>{console.error(error);process.exit(1);});
