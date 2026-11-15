const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
let checks=0;
function fixture(saved){
  const storage=new Map([['agy-codex-owner','owner-a']]);if(saved!==undefined)storage.set('agy-bound-activity-v1',JSON.stringify(saved));
  const a={id:'chat-a',codex_thread_id:'owner-a',turns:[{id:'turn-a1',status:'completed'}]},b={id:'chat-b',codex_thread_id:'owner-b',turns:[{id:'turn-b1',status:'completed'}]};
  const nodes={prompt:{value:''},'history-menu':{hidden:true},'history-dialog':{open:false},'bulk-dialog':{open:false},'image-preview':{open:false},'session-dialog':{open:false},'related-dialog':{open:false}},jumps=[],notices=[];
  const ctx={snapshot:{conversations:[a,b]},viewMode:'home',activeId:null,submitting:false,bulkMode:false,openSelectId:null,selectedBranch:()=>null,boundActivityCursor:null,boundActivityPending:null,
    conversationPages:new Map(),pageCacheKey:id=>id,document:{hidden:false},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},$:id=>nodes[id],
    isBusy:s=>['queued','running','cancelling'].includes(s),selectConversation:id=>{jumps.push(id);ctx.activeId=id;ctx.viewMode='panel';},toast:message=>notices.push(message)};
  vm.createContext(ctx);vm.runInContext(between('function fixedOwnerConversation()','function clearCodePrefix'),ctx);
  return {ctx,a,b,nodes,storage,jumps,notices};
}
function test(name,fn){fn();checks++;console.log('PASS '+name);}
test('only the tab owner can trigger a jump',()=>{
  const {ctx,a,b,jumps}=fixture();assert.equal(ctx.followBoundActivity(),false);
  b.turns.push({id:'turn-b2',status:'running'});assert.equal(ctx.followBoundActivity(),false);
  a.turns.push({id:'turn-a2',status:'queued'});assert.equal(ctx.followBoundActivity(),true);assert.deepEqual(jumps,['chat-a']);
});
test('progress and manual departure do not repeatedly pull the user back',()=>{
  const {ctx,a,jumps}=fixture();ctx.followBoundActivity();a.turns.push({id:'turn-a2',status:'queued'});ctx.followBoundActivity();
  ctx.viewMode='activity';ctx.activeId=null;a.turns.at(-1).status='running';assert.equal(ctx.followBoundActivity(),false);
  a.turns.at(-1).status='completed';assert.equal(ctx.followBoundActivity(),false);assert.equal(jumps.length,1);
});
test('fast completed, failed and cancelled new tasks are still visible',()=>{
  for(const status of ['completed','failed','cancelled']){const {ctx,a}=fixture();ctx.followBoundActivity();a.turns.push({id:'turn-a2',status});assert.equal(ctx.followBoundActivity(),true);}
});
test('an empty-chat baseline catches its first fast task',()=>{
  const {ctx,a,storage}=fixture();a.turns=[];assert.equal(ctx.followBoundActivity(),false);assert.equal(JSON.parse(storage.get('agy-bound-activity-v1')).turnId,null);
  a.turns.push({id:'turn-first',status:'completed'});assert.equal(ctx.followBoundActivity(),true);
});
test('initial historical replies stay put; initially active task follows once',()=>{
  const idle=fixture();assert.equal(idle.ctx.followBoundActivity(),false);
  const running=fixture();running.a.turns.at(-1).status='running';assert.equal(running.ctx.followBoundActivity(),true);
});
test('disabled auto-open suppresses follow and clears deferred jumps without stopping tasks',()=>{
  const {ctx,a,nodes,jumps}=fixture();ctx.followBoundActivity();nodes.prompt.value='draft';a.turns.push({id:'turn-a2',status:'running'});
  assert.equal(ctx.followBoundActivity(),false);assert.ok(ctx.boundActivityPending);
  a.auto_open_activity=false;nodes.prompt.value='';assert.equal(ctx.followBoundActivity(),false);assert.equal(ctx.boundActivityPending,null);
  assert.equal(a.turns.at(-1).status,'running');assert.equal(jumps.length,0);
  a.auto_open_activity=true;assert.equal(ctx.followBoundActivity(),false,'do not revive a task ignored while disabled');
  a.turns.push({id:'turn-a3',status:'queued'});assert.equal(ctx.followBoundActivity(),true);assert.deepEqual(jumps,['chat-a']);
});
test('reload reuses the per-tab cursor and notices work while disconnected',()=>{
  const same=fixture({owner:'owner-a',turnId:'turn-a1'});assert.equal(same.ctx.followBoundActivity(),false);
  const newer=fixture({owner:'owner-a',turnId:'older'});assert.equal(newer.ctx.followBoundActivity(),true);
});
test('no guessed identity on an unbound shared root',()=>{
  const {ctx,a,storage}=fixture();storage.delete('agy-codex-owner');a.turns.at(-1).status='running';assert.equal(ctx.followBoundActivity(),false);
  ctx.rememberFixedOwner(a);assert.equal(storage.get('agy-codex-owner'),'owner-a');assert.equal(ctx.followBoundActivity(),true);
});
test('browsing another fixed chat preserves the original tab owner',()=>{
  const {ctx,a,b,storage,jumps}=fixture();ctx.followBoundActivity();ctx.rememberFixedOwner(b);assert.equal(storage.get('agy-codex-owner'),'owner-a');
  ctx.viewMode='panel';ctx.activeId=b.id;a.turns.push({id:'turn-a2',status:'running'});assert.equal(ctx.followBoundActivity(),true);assert.deepEqual(jumps,['chat-a']);
});
test('already viewing the owner preserves older-page position',()=>{
  const {ctx,a,jumps}=fixture();ctx.viewMode='panel';ctx.activeId=a.id;ctx.followBoundActivity();a.turns.push({id:'turn-a2',status:'running'});
  assert.equal(ctx.followBoundActivity(),false);assert.equal(jumps.length,0);
});
test('reading an older history or search page never jumps to another work session',()=>{
  const {ctx,a,b,jumps}=fixture();ctx.followBoundActivity();ctx.viewMode='panel';ctx.activeId=b.id;
  ctx.conversationPages.set(b.id,{value:{page:{has_newer:true}}});a.turns.push({id:'turn-new',status:'running',branch:'new-work'});
  assert.equal(ctx.followBoundActivity(),false);assert.equal(jumps.length,0);assert.ok(ctx.boundActivityPending);
  ctx.conversationPages.get(b.id).value.page.has_newer=false;
  assert.equal(ctx.followBoundActivity(),true);assert.deepEqual(jumps,['chat-a']);
});
test('drafts, dialogs, menus, bulk selection, submit and hidden page defer rather than lose the event',()=>{
  for(const guard of ['draft','history-dialog','bulk-dialog','image-preview','session-dialog','related-dialog','submit','hidden','bulk-mode','select-menu','context-menu']){
    const {ctx,a,nodes,jumps}=fixture();ctx.followBoundActivity();a.turns.push({id:'turn-a2',status:'running'});
    if(guard==='draft')nodes.prompt.value='keep my draft';else if(guard==='submit')ctx.submitting=true;else if(guard==='hidden')ctx.document.hidden=true;else if(guard==='bulk-mode')ctx.bulkMode=true;else if(guard==='select-menu')ctx.openSelectId='model';else if(guard==='context-menu')nodes['history-menu'].hidden=false;else nodes[guard].open=true;
    assert.equal(ctx.followBoundActivity(),false);assert.equal(jumps.length,0);assert.ok(ctx.boundActivityPending);
    if(guard==='draft'){assert.equal(nodes.prompt.value,'keep my draft');nodes.prompt.value='';}else if(guard==='submit')ctx.submitting=false;else if(guard==='hidden')ctx.document.hidden=false;else if(guard==='bulk-mode')ctx.bulkMode=false;else if(guard==='select-menu')ctx.openSelectId=null;else if(guard==='context-menu')nodes['history-menu'].hidden=true;else nodes[guard].open=false;
    assert.equal(ctx.followBoundActivity(),true);assert.equal(ctx.followBoundActivity(),false);
  }
});
test('manual arrival consumes a deferred event without scrolling',()=>{
  const {ctx,a,nodes,jumps}=fixture();ctx.followBoundActivity();nodes.prompt.value='draft';a.turns.push({id:'turn-a2',status:'running'});ctx.followBoundActivity();
  ctx.activeId=a.id;ctx.viewMode='panel';nodes.prompt.value='';assert.equal(ctx.followBoundActivity(),false);assert.equal(jumps.length,0);assert.equal(ctx.boundActivityPending,null);
});
test('new activity in another work session follows that session within the same entry',()=>{
  const {ctx,a}=fixture();a.primary_branch='main';a.branches={main:{},art:{}};a.turns[0].branch='main';ctx.activeId=a.id;ctx.activeBranch='main';ctx.viewMode='panel';
  ctx.selectedBranch=()=>ctx.activeBranch;let opened=null;ctx.selectConversation=(id,branch)=>{opened={id,branch};ctx.activeBranch=branch;};
  ctx.followBoundActivity();a.turns.push({id:'new-art-task',branch:'art',status:'queued'});
  assert.equal(ctx.followBoundActivity(),true);assert.deepEqual(opened,{id:'chat-a',branch:'art'});assert.equal(ctx.followBoundActivity(),false);
});
test('first visit follows a running session ahead of a later cancelled or completed turn',()=>{
  const {ctx,a}=fixture();a.turns=[{id:'running',branch:'active',status:'running'},{id:'later',branch:'finished',status:'cancelled'}];
  let target=null;ctx.selectConversation=(id,branch)=>target=branch;
  assert.equal(ctx.followBoundActivity(),true);assert.equal(target,'active');
});
(async()=>{
  const {ctx}=fixture();let follows=0,details=0;
  Object.assign(ctx,{stateRefreshPromise:null,api:async()=>null,online:true,followBoundActivity:()=>{follows++;return true;},synchronizeDetail:()=>details++,render:()=>{throw Error('extra render');}});
  vm.runInContext(between('function refresh(afterChange=true)','async function newConversation'),ctx);await ctx.refresh(false);
  assert.equal(follows,1);assert.equal(details,1);assert.equal(ctx.stateRefreshPromise,null);checks++;
  console.log('PASS a 304 state response resumes a deferred jump without adding a timer');
  console.log('Bound activity follow checks passed: '+checks+' behavior groups.');
})().catch(error=>{console.error(error);process.exit(1);});
