const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const escaped=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function node(){return {hidden:true,open:false,textContent:'',innerHTML:'',disabled:false,style:{},offsetWidth:160,offsetHeight:30,replaceChildren(){this.innerHTML='';this.textContent='';}};}

function tooltipTests(){
  const box=node(),timers=new Map();let id=0;
  const ctx={quickTooltipTimer:null,quickTooltipTarget:null,accountExpanded:false,openSelectId:null,document:{hidden:false},window:{innerWidth:320,innerHeight:200},
    $:()=>box,setTimeout(fn,delay){assert.equal(delay,300);timers.set(++id,fn);return id;},clearTimeout(key){timers.delete(key);}};
  vm.createContext(ctx);vm.runInContext(between('function hideQuickTooltip','function quotaGroupsForModel'),ctx);
  const attrs=new Map([['aria-describedby','original-label']]);let popover=false;
  Object.assign(box,{matches:()=>popover,showPopover(){popover=true;},hidePopover(){popover=false;}});
  const target={isConnected:true,dataset:{tooltip:'每周 · 剩余 78%'},getAttribute:k=>attrs.get(k),setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k),getBoundingClientRect:()=>({left:290,top:5,width:30,bottom:35})};
  ctx.scheduleQuickTooltip(target);assert.equal(box.hidden,true);assert.equal(timers.size,1);
  ctx.scheduleQuickTooltip(target);assert.equal(timers.size,1);
  const fire=()=>{const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());};
  fire();assert.equal(box.hidden,false);assert.equal(box.textContent,target.dataset.tooltip);assert.equal(box.style.left,'152px');assert.equal(box.style.top,'44px');
  assert.equal(popover,true);assert.equal(attrs.get('aria-describedby'),'original-label quick-tooltip');
  ctx.hideQuickTooltip();assert.equal(box.hidden,true);assert.equal(popover,false);assert.equal(attrs.get('aria-describedby'),'original-label');
  ctx.scheduleQuickTooltip(target);ctx.hideQuickTooltip();fire();assert.equal(box.hidden,true);
  ctx.accountExpanded=true;ctx.scheduleQuickTooltip(target);fire();assert.equal(box.hidden,false,'account details also use the shared tooltip');ctx.hideQuickTooltip();
  for(const blocker of ['menu','hidden','detached']){
    ctx.hideQuickTooltip();ctx.accountExpanded=blocker==='account';ctx.openSelectId=blocker==='menu'?'model':null;
    ctx.document.hidden=blocker==='hidden';target.isConnected=blocker!=='detached';
    ctx.scheduleQuickTooltip(target);fire();assert.equal(box.hidden,true,blocker);
  }
}

async function auditTests(){
  const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  get('collaboration-audit-dialog').open=true;
  const calls=[];let resolve;
  const ctx={$:get,URLSearchParams,activeId:'current',escape:escaped,texts:{completed:'已完成',failed:'未完成'},isBusy:s=>s==='running',timeLabel:String,duration:String,
    collaborationAudit:{cid:'owner',sequence:0,records:[],before:null,hasOlder:false,scanned:0,pending:false},
    api:(path)=>{calls.push(path);return new Promise(r=>resolve=r);}};
  vm.createContext(ctx);vm.runInContext(between('function collaborationRecord','function rememberSessionResult'),ctx);
  const turn={id:'a'.repeat(32),source:'codex',prompt:'Review <script>',response:'Reply <img>',model:'Flash',status:'completed',created_at:'now',usage_scope:'turn',usage:{input_tokens:80,output_tokens:20,cache_read_tokens:60}};
  const first=ctx.loadCollaborationAudit();assert.ok(calls[0].includes('source=codex'));assert.ok(calls[0].startsWith('/api/conversations/owner?'));
  await ctx.loadCollaborationAudit();assert.equal(calls.length,1);
  ctx.activeId='different';resolve({turns:[turn,{...turn,id:'b'.repeat(32),source:'web'}],page:{source:'codex',first_id:turn.id,has_older:true}});await first;
  assert.equal(ctx.collaborationAudit.records.length,1);assert.equal(ctx.collaborationAudit.filtered,true);
  assert.ok(get('collaboration-audit-list').innerHTML.includes('&lt;script&gt;'));assert.ok(!get('collaboration-audit-list').innerHTML.includes('<img>'));
  assert.ok(get('collaboration-audit-list').innerHTML.includes('缓存读取 60'));
  const detail=ctx.showCollaborationTurn(turn.id);assert.equal(calls.at(-1),'/api/conversations/owner/turns/'+turn.id);
  resolve({turn:{...turn,completion_evidence:{source:'official_recovered_api_error',raw_cli_error:'error <script>'}}});await detail;
  assert.ok(get('collaboration-audit-detail').innerHTML.includes('完成状态核验依据'));
  assert.ok(get('collaboration-audit-detail').innerHTML.includes('&lt;script&gt;'));assert.ok(!get('collaboration-audit-detail').innerHTML.includes('<script>'));
  const earlier=ctx.loadCollaborationAudit(true);assert.ok(calls.at(-1).includes('before='+turn.id));
  ctx.collaborationAudit.sequence++;ctx.collaborationAudit.pending=false;get('collaboration-audit-dialog').open=false;
  const before=get('collaboration-audit-list').innerHTML;
  resolve({turns:[{...turn,id:'c'.repeat(32)}],page:{source:'codex'}});await earlier;
  assert.equal(get('collaboration-audit-list').innerHTML,before);
  get('collaboration-audit-dialog').open=true;
  const refresh=ctx.loadCollaborationAudit();resolve({turns:Array.from({length:80},(_,i)=>({...turn,id:i.toString(16).padStart(32,'0'),prompt:'x'.repeat(10000),response:'y'.repeat(10000)})),page:{source:'codex',has_older:true}});await refresh;
  assert.equal(ctx.collaborationAudit.records.length,60);assert.equal(ctx.collaborationAudit.records[0].request.length,500);assert.equal(ctx.collaborationAudit.records[0].response.length,500);
  assert.equal(get('collaboration-audit-older').hidden,true);
}

(async()=>{tooltipTests();await auditTests();console.log('Console details passed: 300 ms tooltip, top layer, accessible association, cancellation, viewport bounds, filtered collaboration records, exact chat target, escaped raw replies, bounded cache, stale-response suppression.');})().catch(e=>{console.error(e);process.exit(1);});
