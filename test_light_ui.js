const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
class Element{
  constructor(html=''){this.html=html;this.children=[];this.parentNode=null;this.dataset={};}
  get firstElementChild(){return this.children[0]||null;}
  get nextElementSibling(){if(!this.parentNode)return null;return this.parentNode.children[this.parentNode.children.indexOf(this)+1]||null;}
  remove(){if(this.parentNode){const list=this.parentNode.children;list.splice(list.indexOf(this),1);this.parentNode=null;}}
  insertBefore(node,anchor){if(node===anchor)return;node.remove();const index=anchor?this.children.indexOf(anchor):this.children.length;assert.ok(index>=0);this.children.splice(index,0,node);node.parentNode=this;}
  replaceChildren(){for(const node of this.children)node.parentNode=null;this.children=[];}
}
(async()=>{
  const container=new Element(),copies=new Map(),builds={};
  const dom={renderedMessageView:'',renderInstance:0,renderedMessageRows:new Map(),codeCopies:copies,$:()=>container,
    document:{createElement:()=>{const content=new Element();return {content,set innerHTML(value){const node=new Element(value);content.insertBefore(node,null);}};}}};
  vm.createContext(dom);vm.runInContext(between('function clearCodePrefix','function pageTag'),dom);
  const row=(key,text)=>({key,codePrefix:key,signature:text,html:()=>{builds[key]=(builds[key]||0)+1;copies.set(key+'-code',text);return '<article>'+text+'</article>';}});
  dom.syncMessageRows('chat-a',[row('complete','old code'),row('stream','part one')]);
  const oldNode=container.children[0],streamNode=container.children[1];
  dom.syncMessageRows('chat-a',[row('complete','old code'),row('stream','part two')]);
  assert.equal(container.children[0],oldNode);assert.notEqual(container.children[1],streamNode);
  assert.equal(builds.complete,1);assert.equal(copies.get('complete-code'),'old code');assert.equal(copies.get('stream-code'),'part two');
  dom.syncMessageRows('chat-a',[row('stream','part two')]);assert.equal(copies.has('complete-code'),false);assert.equal(container.children.length,1);
  dom.clearMessageRows();assert.equal(copies.size,0);assert.equal(container.children.length,0);
  const metas=new Map([['a',{id:'a',summary:true,revision:1,turns:[]}],['b',{id:'b',summary:true,revision:1,turns:[]}]]),waiting=[],renders=[];
  let activeRequests=0,peak=0;
  const paging={URLSearchParams,sessionGroups:()=>false,selectedBranch:()=>null,PAGE_SIZE:20,conversationPages:new Map(),conversationRequests:new Map(),conversationIntents:new Map(),activeId:'a',viewMode:'panel',
    conversationMeta:id=>metas.get(id),api:()=>{activeRequests++;peak=Math.max(peak,activeRequests);return new Promise(resolve=>waiting.push(value=>{activeRequests--;resolve(value);}));},
    render:()=>{const value=paging.conversationPages.get(paging.activeId)?.value;if(value)renders.push(value.label);}};
  vm.createContext(paging);vm.runInContext(between('function pageTag','function positionResources'),paging);
  const first=paging.loadConversationPage('a',{before:'old'},false);await flush();
  const latest=paging.loadConversationPage('a',{},true);waiting.shift()({id:'a',label:'stale old page',turns:[]});await first;await flush();
  assert.ok(!renders.includes('stale old page'));assert.equal(waiting.length,1);
  waiting.shift()({id:'a',label:'latest',turns:[]});await latest;assert.ok(renders.includes('latest'));assert.equal(peak,1);
  const cached=paging.conversationPages.get('a');cached.error='temporary failure';cached.retryAt=0;
  const retry=paging.loadConversationPage('a',{});await flush();assert.equal(waiting.length,1);
  waiting.shift()({id:'a',label:'recovered',turns:[]});await retry;assert.equal(paging.conversationPages.get('a').error,'');
  const turns=Array.from({length:55},(_,i)=>({id:String(i),status:'completed',response:'text'}));
  const page=paging.localConversationPage({id:'c',turns},{});assert.equal(page.turns.length,20);assert.equal(page.page.start,36);
  const older=paging.localConversationPage({id:'c',turns},{before:page.page.first_id});turns.push({id:'55'});
  const stable=paging.localConversationPage({id:'c',turns},{before:page.page.first_id});assert.deepEqual(Array.from(stable.turns,t=>t.id),Array.from(older.turns,t=>t.id));
  const busy={id:'running-old',status:'running'},summary={id:'a',turns:[busy]},pageConv={id:'a',turns:[{status:'completed'}]};
  const control={conversationMeta:()=>summary,selectedBranch:()=>null,isBusy:s=>s==='running'};vm.createContext(control);vm.runInContext(between('function composerTurn','function fixedOwnerConversation'),control);
  assert.equal(control.composerTurn(pageConv).id,'running-old');
  let resourceCalls=0,resolveResource;
  const resources={resourcePending:false,resourceExpanded:true,resourceData:null,resourceError:'',paintResources:()=>{},api:()=>{resourceCalls++;return new Promise(resolve=>resolveResource=resolve);}};
  vm.createContext(resources);vm.runInContext(between('async function readResources','function historySettings'),resources);
  const read=resources.readResources();resources.readResources();resources.closeResources();resolveResource({processes:[]});await read;await flush();
  assert.equal(resourceCalls,1);assert.equal(resources.resourceExpanded,false);assert.equal(resources.resourcePending,false);
  console.log('Light UI checks passed: retained DOM/code copy, bounded pages, stale response rejection, global task controls, on-demand resource reads.');
})().catch(error=>{console.error(error);process.exit(1);});
