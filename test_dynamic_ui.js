const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  for(const [chosen,lastKind,otherKind,groups,expected] of [['art','image','text',true,'image'],['art','text','image',true,'text'],['art',null,'image',true,'text'],['main',null,'image',false,'image']]){
    const meta={id:'mode',summary:true,primary_branch:'main',branches:{main:{id:'main'},art:{id:'art',last_kind:lastKind}},turns:[{id:'other',branch:'main',kind:otherKind}]},nodes={'task-kind':{value:''}};
    const mode={lastServiceInstance:null,restoreFirstDraft:()=>{},snapshot:{service:{features:{session_groups:groups}},conversations:[meta]},activeId:'mode',activeBranch:chosen,conversationPages:new Map(),stateRefreshPromise:null,online:true,requestedCodexThread:null,boundViewApplied:true,kindRestored:false,viewMode:'panel',
      $:id=>nodes[id],api:async()=>mode.snapshot,followBoundActivity:()=>false,render:()=>{},synchronizeDetail:()=>{}};
    vm.createContext(mode);vm.runInContext(between('function conversationMeta','function composerTurn'),mode);vm.runInContext(between('function pageCacheKey','function localConversationPage'),mode);vm.runInContext(between('function refresh(afterChange','async function newConversation'),mode);
    await mode.refresh();assert.equal(nodes['task-kind'].value,expected,'reload restores the selected work session mode, including legacy fallback');
    nodes['task-kind'].value=expected==='image'?'text':'image';const manual=nodes['task-kind'].value;await mode.refresh();assert.equal(nodes['task-kind'].value,manual,'polling retains a manually chosen task mode');
  }
  const conv={id:'a',summary:true,revision:'boot:1',primary_branch:'main',branches:{main:{id:'main'},art:{id:'art'},notes:{id:'notes'}},turns:[]},waiting=[],renders=[];
  const ctx={URLSearchParams,PAGE_SIZE:20,conversationPages:new Map(),conversationRequests:new Map(),conversationIntents:new Map(),activeId:'a',activeBranch:'main',viewMode:'panel',
    sessionGroups:()=>true,conversationMeta:id=>id==='a'?conv:null,selectedBranch:()=>ctx.activeBranch,
    api:path=>new Promise(resolve=>waiting.push({path,resolve})),render:()=>renders.push(ctx.conversationPages.get(ctx.pageCacheKey('a'))?.value?.label)};
  vm.createContext(ctx);vm.runInContext(between('function pageTag','function positionResources'),ctx);
  const main=ctx.loadConversationPage('a',{},true);await flush();assert.ok(waiting[0].path.includes('branch=main'));
  ctx.activeBranch='art';const art=ctx.loadConversationPage('a',{},true);await flush();assert.ok(waiting[1].path.includes('branch=art'));
  waiting[1].resolve({label:'art page',turns:[{id:'art-turn'}],page:{total:1}});await art;
  waiting[0].resolve({label:'main page',turns:[{id:'main-turn'}],page:{total:1}});await main;
  assert.equal(renders.at(-1),'art page');assert.ok(!renders.includes('main page'));
  ctx.activeBranch='main';await ctx.loadConversationPage('a');assert.equal(ctx.conversationPages.get('a:main').value.label,'main page');assert.equal(waiting.length,2);
  ctx.activeBranch='notes';const notes=ctx.loadConversationPage('a',{},true);await flush();waiting[2].resolve({label:'notes page',turns:[],page:{total:0}});await notes;
  assert.equal(ctx.conversationPages.size,2);assert.ok(ctx.conversationPages.has('a:notes'));
  const all={id:'a',turns:[{id:'m',branch:'main'},{id:'a',branch:'art'},{id:'m2',branch:'main'}]};
  const page=ctx.localConversationPage(all,{branch:'art'});assert.deepEqual(Array.from(page.turns,t=>t.id),['a']);assert.equal(page.page.total,1);
  const control={selectedBranch:()=> 'art',conversationMeta:()=>({turns:[{id:'m',branch:'main',status:'running'},{id:'a',branch:'art',status:'queued'}]}),isBusy:s=>['queued','running'].includes(s)};
  vm.createContext(control);vm.runInContext(between('function composerTurn','function fixedOwnerConversation'),control);assert.equal(control.composerTurn({id:'a'}).id,'a');
  const nodes={prompt:{value:'main draft',style:{}},'task-kind':{value:''},conversation:{scrollTop:0,scrollHeight:100,scrollTo({top}){this.scrollTop=top;}}};
  const storage=new Map(),drafts=new Map();const model={id:'a',codex_thread_id:'thread',primary_branch:'main',branches:{main:{id:'main',last_kind:'text'},art:{id:'art',last_kind:'image'}}};
  const other={id:'b',primary_branch:'main',branches:{main:{id:'main'}}};
  const draft={draftStore:{write:()=>true,read:()=>'',clearSent:()=>{}},draftPending:null,draftSaveTimer:null,draftStorageWarned:false,clearTimeout,requestedCodexThread:null,activeId:'a',activeBranch:'main',viewMode:'panel',selectedHistory:new Set(),sessionDrafts:drafts,conversationPages:new Map(),sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
    conversationMeta:id=>id==='b'?other:model,composerConversation:()=>draft.viewMode==='panel'?(draft.activeId==='b'?other:model):model,pageCacheKey:id=>id+':'+(id===draft.activeId?draft.activeBranch:'main'),rememberFixedOwner:()=>{},setViewAddress:params=>draft.address=params,
    closeImagePreview:()=>{},closeHistoryMenu:()=>{},closeSelectMenu:()=>{},revealHistorySource:()=>{},updateTaskMode:()=>{},sessionGroups:()=>true,render:()=>{},loadConversationPage:()=>{},setSidebarOpen:()=>{},startPolling:()=>{},requestAnimationFrame:fn=>fn(),$:id=>nodes[id]};
  draft.selectedBranch=c=>c.id===draft.activeId?draft.activeBranch:c.primary_branch;
  vm.createContext(draft);vm.runInContext(between('function restoredTaskKind','function composerTurn'),draft);
  vm.runInContext(between('function composerKey','function rememberFixedOwner'),draft);vm.runInContext(between('function selectConversation(id','function renderComposer'),draft);
  draft.selectConversation('a','art');assert.equal(nodes.prompt.value,'');assert.equal(nodes['task-kind'].value,'image');assert.equal(draft.address.session,'art');
  nodes.prompt.value='art draft';draft.selectConversation('a','main');assert.equal(nodes.prompt.value,'main draft');assert.equal(draft.address.session,undefined);
  draft.selectConversation('a','art');assert.equal(nodes.prompt.value,'art draft');assert.equal(draft.sessionDrafts.size,2);
  draft.selectConversation('b','main');nodes.prompt.value='other chat draft';
  vm.runInContext(between('function showHome()','function showActivity()'),draft);draft.showHome();assert.notEqual(nodes.prompt.value,'other chat draft');
  nodes.prompt.value='home draft for owner';draft.selectConversation('a','main');assert.equal(nodes.prompt.value,'home draft for owner');
  draft.selectConversation('b','main');assert.equal(nodes.prompt.value,'other chat draft');
  const stale={id:'stale',branches:{},primary_branch:null},optimistic={conversationMeta:()=>stale};
  vm.createContext(optimistic);vm.runInContext(between('function rememberSessionResult','function openSessionDialog'),optimistic);
  optimistic.rememberSessionResult('stale',{id:'new',title:'new session',agy_id:null});assert.equal(stale.primary_branch,'new');assert.ok(stale.branches.new);
  stale.branches.new={id:'new',agy_id:'already-initialized',turn_count:2};optimistic.rememberSessionResult('stale',{id:'new',agy_id:null});
  assert.equal(stale.branches.new.agy_id,'already-initialized');assert.equal(stale.branches.new.turn_count,2);
  console.log('Dynamic UI checks passed: reload mode isolation, manual mode preservation, session-keyed pages, no stale branch rendering, two-page cache, scoped controls, independent drafts and same-page addresses.');
})().catch(error=>{console.error(error);process.exit(1);});
