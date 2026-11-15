const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),nodes=new Map(),requests=[];
const node=id=>{if(!nodes.has(id))nodes.set(id,{open:true,hidden:false,setAttribute(k,v){this[k]=v;}});return nodes.get(id);};
const chats={a:{id:'a',primary_branch:'main',branches:{main:{title:'Main'}}},b:{id:'b',primary_branch:'main',branches:{main:{title:'Other'}}}};
let resources=0;
const ctx={$:node,viewMode:'panel',activeId:'a',conversationMeta:id=>chats[id],selectedBranch:()=> 'main',
  active:()=>({id:ctx.activeId,page:{branch:'main'},turns:[{},{}]}),online:true,
  settingsInsightsExpanded:true,settingsInsights:{key:null,sequence:0,data:null,error:'',pending:false},
  resourcePending:false,resourceData:null,resourceError:'',texts:{completed:'已完成',failed:'未完成'},
  escape:s=>String(s).replaceAll('<','&lt;'),accountCheckedLabel:v=>v||'—',tokenUsageText:t=>(t.usage_scope==='turn'?'本轮':'会话累计')+' 输入 '+t.usage.input_tokens,
  URLSearchParams,api:path=>new Promise((resolve,reject)=>requests.push({path,resolve,reject})),
  readResources:async()=>{resources++;}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function settingsInsightsScope'),source.indexOf('function hideQuickTooltip')),ctx);
const result=(cid,count=30)=>({conversation_id:cid,branch:'main',session_title:'Main',turn_count:count,manual_compaction:null,latest_usage:{usage:{input_tokens:50},usage_scope:'turn',status:'failed'}});
(async()=>{
  ctx.renderSettingsInsights();assert.equal(requests.length,0);assert.equal(resources,0);
  const old=ctx.readSettingsInsights();assert.ok(requests[0].path.includes('/a/context?branch=main'));
  ctx.activeId='b';const current=ctx.readSettingsInsights();
  requests[1].resolve(result('b',4));await current;
  requests[0].resolve(result('a'));await old;
  assert.equal(ctx.settingsInsights.data.conversation_id,'b');
  assert.ok(node('settings-context-body').innerHTML.includes('4 轮'));
  assert.ok(node('settings-context-body').innerHTML.includes('未找到 /compact'));
  assert.ok(node('settings-context-body').innerHTML.includes('本轮 输入 50'));
  assert.ok(node('settings-context-body').innerHTML.includes('未完成'));
  ctx.resourceData={processes:[{role:'控制台',footprint_mb:12.4,rss_mb:4},{role:'Agy 任务',footprint_mb:null,rss_mb:6.8}],checked_at:'now'};
  ctx.renderSettingsInsights();const memory=node('settings-memory-body').innerHTML;
  assert.ok(memory.includes('12.4 MB · 物理内存'));assert.ok(memory.includes('6.8 MB · RSS'));
  assert.ok(memory.includes('独占内存暂无法'));
  const closing=ctx.readSettingsInsights();node('settings-dialog').open=false;
  requests[2].resolve(result('b',99));await closing;
  assert.equal(ctx.settingsInsights.data,null);
  node('settings-dialog').open=true;const mismatch=ctx.readSettingsInsights();
  requests[3].resolve(result('a'));await mismatch;
  assert.equal(ctx.settingsInsights.data,null);assert.ok(ctx.settingsInsights.error.includes('会话不匹配'));
  node('settings-dialog').open=true;ctx.viewMode='activity';await ctx.readSettingsInsights();
  assert.equal(requests.length,4);assert.ok(node('settings-context-body').innerHTML.includes('进入聊天'));
  console.log('Settings insights passed: on-demand reads, session/race isolation, unknown compression, usage scopes, physical/RSS and browser limits.');
})().catch(error=>{console.error(error);process.exitCode=1;});
