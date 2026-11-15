const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(__dirname+'/app.js','utf8'),actions=fs.readFileSync(__dirname+'/panel_actions.js','utf8');
const between=(source,a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const escaped=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function fixture(){
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',innerHTML:'',checked:false,hidden:false,open:false,disabled:false,options:[{}],classList:{add(){}},replaceChildren(){this.innerHTML='';},close(){this.open=false;},showModal(){this.open=true;},focus(){}});return nodes.get(id);};
  const calls=[],selections=[],conv={id:'a'.repeat(32),title:'Current',branches:{main:{title:'Main'}},turns:[]};
  const context={$,URLSearchParams,escape:escaped,texts:{failed:'未完成'},orderedModels:m=>m,isBusy:s=>['queued','running','cancelling'].includes(s),
    snapshot:{service:{features:{task_recovery:true}},health:{models:[{id:'flash',label:'Flash <safe>'}]}},uiSettings:{font:14,idleMs:12000,modelOrder:[]},historyPins:new Set(),
    activeId:conv.id,viewMode:'panel',conversationMeta:id=>id===conv.id?conv:null,online:true,accountExpanded:false,
    api:async(path,body)=>{calls.push({path,body});return {id:conv.id,turn:{id:'old',branch:'main',status:'failed',error:'old error',response:'partial'}};},
    renderAccount(){},closeSelectMenu(){},hideQuickTooltip(){},renderSettings(){},setSidebarOpen(){},toast(){},
    conversationPages:new Map(),pageCacheKey:(cid,params)=>cid+':'+(params?.branch||'main'),refresh:async()=>{},
    selectConversation:(id,branch)=>selections.push({id,branch}),accountCheckedLabel:String,
    loadConversationPage:async(id,params,manual)=>calls.push({id,params,manual}),
  };
  vm.createContext(context);vm.runInContext(actions.slice(0,actions.indexOf("$('panel-action-form').addEventListener")),context);
  return {context,$,calls,selections,conv};
}
async function recoveryTests(){
  const {context,$,calls,selections,conv}=fixture();
  await context.recoverTask('old');
  assert.equal($('panel-action-dialog').open,true);assert.equal($('recovery-strategy').value,'continue');
  assert.equal($('recovery-previous').textContent,'old error\n\npartial');
  assert.ok($('recovery-model').innerHTML.includes('&lt;safe&gt;'));
  $('recovery-model').value='flash';$('panel-action-ack').checked=true;context.activeId='other';
  let resolve;context.api=(path,body)=>{calls.push({path,body});return new Promise(r=>resolve=r);};
  const pending=context.submitPanelAction({preventDefault(){}});
  await context.submitPanelAction({preventDefault(){}});
  assert.equal(calls.filter(x=>x.body).length,1,'double click does not enqueue twice');
  assert.equal(calls.at(-1).path,'/api/conversations/'+conv.id+'/turns/old/recover');
  assert.equal(calls.at(-1).body.model,'flash');assert.equal(calls.at(-1).body.acknowledge,true);
  resolve({});await pending;assert.equal(selections[0].id,conv.id);assert.equal($('panel-action-dialog').open,false);
  assert.equal(context.snapshot.preferred_model,undefined,'per-task override never saves global model');
}
async function staleDialogTests(){
  const {context,$}=fixture();let resolve;context.api=()=>new Promise(r=>resolve=r);
  const pending=context.recoverTask('old');context.activeId='other';resolve({turn:{status:'failed'}});await pending;
  assert.equal($('panel-action-dialog').open,false,'a slow reply cannot open another chat recovery dialog');
}
async function searchTests(){
  const {context,$,conv,calls,selections}=fixture();
  $('history-search').value='literal [x]';context.openFulltext();
  assert.equal($('fulltext-dialog').open,true);assert.equal($('fulltext-scope').value,'current');
  $('fulltext-query').value='<script>[x].*';
  const result={conversation_id:conv.id,branch:'main',turn_id:'old-turn',title:'<script>',session_title:'Main',role:'assistant',snippet:'text <script>[x].* end'};
  let resolve;context.api=(path)=>{calls.push({path});return new Promise(r=>resolve=r);};
  const pending=context.searchFulltext({preventDefault(){}});await context.searchFulltext({preventDefault(){}});
  assert.equal(calls.length,1);assert.ok(calls[0].path.includes('conversation='+conv.id));
  resolve({results:[result]});await pending;
  assert.ok($('fulltext-results').innerHTML.includes('<mark>&lt;script&gt;[x].*</mark>'));
  assert.ok(!$('fulltext-results').innerHTML.includes('<script>'));
  await context.jumpToSearchResult(0);assert.equal(selections[0].id,conv.id);
  assert.equal(calls.at(-1).params.around,'old-turn');assert.equal(calls.at(-1).params.matchRole,'assistant');
  assert.equal(calls.at(-1).manual,true);
  context.openFulltext();const stale=context.searchFulltext({preventDefault(){}});
  vm.runInContext('fulltextState.sequence++;',context);$('fulltext-dialog').open=false;
  const before=$('fulltext-results').innerHTML;resolve({results:[result]});await stale;
  assert.equal($('fulltext-results').innerHTML,before,'dismissed search cannot receive stale results');
}
function paginationAndProofTests(){
  const {context,conv}=fixture();Object.assign(context,{PAGE_SIZE:20});
  vm.runInContext(between(app,'function localConversationPage','function pruneConversationPages'),context);
  const meta={id:'chat',primary_branch:'main',turns:Array.from({length:100},(_,i)=>({id:String(i),branch:i%2?'art':'main'}))};
  const page=context.localConversationPage(meta,{branch:'main',around:'4'});
  assert.equal(page.turns.length,20);assert.equal(page.page.total,50);assert.ok(page.turns.some(t=>t.id==='4'));
  assert.throws(()=>context.localConversationPage(meta,{branch:'art',around:'4'}));
  conv.branches.main.last_turn_id='old';
  assert.ok(context.taskActionMarkup({id:'old',branch:'main',status:'failed'}).includes('恢复任务'));
  assert.ok(!context.taskActionMarkup({id:'earlier',branch:'main',status:'failed'}).includes('恢复任务'));
  assert.ok(!context.taskActionMarkup({id:'ok',status:'completed'}).includes('恢复任务'));
  assert.ok(context.taskActionMarkup({action:'compact',status:'completed'}).includes('检查点未核验'));
  assert.ok(context.taskActionMarkup({action:'compact',status:'completed',compaction_evidence:{source:'cli_checkpoint'}}).includes('检查点已核验'));
}
async function backupTests(){
  const {context,$,calls}=fixture();let resolve;
  context.api=(path,body)=>{calls.push({path,body});return new Promise(r=>resolve=r);};
  $('backup-file').files=[{size:65*1024*1024}];await context.previewBackup();assert.equal(calls.length,0);
  assert.ok($('backup-error').textContent.includes('64 MB'));
  const pending=context.exportBackup();await context.exportBackup();assert.equal(calls.length,1);
  resolve({url:'/api/backups/'+'a'.repeat(32)+'/download',bytes:1024,counts:{conversations:1,turns:2,images:3,bindings:1}});await pending;
  assert.ok($('backup-download').innerHTML.includes('download'));
  assert.ok(!context.backupLink({url:'https://evil.example'},'bad'));
  assert.equal($('backup-file').disabled,false);
  $('backup-ack').checked=true;context.paintBackupBusy();assert.equal($('backup-restore').disabled,true,'no valid preview cannot restore');
  context.api=async()=>{throw new Error('busy tasks');};await context.exportBackup();
  assert.equal($('backup-error').textContent,'busy tasks');assert.equal($('backup-export').disabled,false);
}
(async()=>{await recoveryTests();await staleDialogTests();await searchTests();paginationAndProofTests();await backupTests();console.log('Panel actions passed: exact recovery target, manual confirmation, double-submit guard, scoped/stale search, escaping and literal matching, around-page jump, honest checkpoint status, bounded backup and recoverable errors.');})().catch(error=>{console.error(error);process.exit(1);});
