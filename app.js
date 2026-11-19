'use strict';
const $ = id => document.getElementById(id);
let token = document.querySelector('meta[name="console-token"]').content;
let snapshot = {conversations:[],health:{}}, activeId = sessionStorage.getItem('agy-active'), busy=false, online=true, lastRender='', toastTimer, kindRestored=false, accountRefreshPending=false, accountExpanded=false;
let viewMode=sessionStorage.getItem('agy-view')||'home', externalId=sessionStorage.getItem('agy-external'), activityState={sessions:[],unmatched_processes:[],checked_at:null,warning:''},externalDetail=null,activityPending=false,activityOnline=true,activityFingerprint='';
if(!['home','activity','panel','external'].includes(viewMode))viewMode='home';
let historyRenderKey='',historyMenuEntry=null,historyDialogEntry=null,historyDialogAction='',submitting=false;
const externalTitleCache=new Map();let titleHydrating=false;
const historyPinsKey='agy-history-pins-v1';
function loadHistoryPins(){try{const saved=JSON.parse(localStorage.getItem(historyPinsKey)||'[]');return new Set(Array.isArray(saved)?saved.filter(key=>/^(?:panel:[a-f0-9]{32}|external:[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/.test(key)).slice(0,512):[]);}catch(e){return new Set();}}
let historyPins=loadHistoryPins();
const selectPickers=new Map();let openSelectId=null,selectMenuRender='';
let bulkMode=false,bulkBusy=false,bulkTargets=[],visibleHistory=[],bulkCompleted=0;
let stateRefreshPromise=null,pollEpoch=0,stateETag=null;
const pollTimers=new Map();
let memoryPollSync=null;
const initialAddress=new URLSearchParams(location.search);
let requestedCodexThread=initialAddress.get('codex'),boundViewApplied=false;
if(requestedCodexThread){viewMode='home';activeId=null;}
if(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(requestedCodexThread||''))sessionStorage.setItem('agy-codex-owner',requestedCodexThread);
if(/^[a-f0-9]{32}$/.test(initialAddress.get('chat')||'')){activeId=initialAddress.get('chat');viewMode='panel';}
if(/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(initialAddress.get('agy')||'')){externalId=initialAddress.get('agy');viewMode='external';}
if(initialAddress.get('view')==='activity')viewMode='activity';
const selectedHistory=new Set();
const PAGE_SIZE=20,conversationPages=new Map(),conversationRequests=new Map(),conversationIntents=new Map();
let renderedMessageView='',renderInstance=0;const renderedMessageRows=new Map();
let resourceExpanded=false,resourcePending=false,resourceData=null,resourceError='',liveMemoryLastGood=null,liveMemoryPending=false,liveMemoryError='';
let boundActivityCursor=null,boundActivityPending=null;
let activeBranch=initialAddress.get('session')||null,nextFollowBranch=null,relatedExpanded=false,sessionDialogState=null,relatedRequest=0;
const sessionDrafts=new Map();
class DraftStore{
  constructor(storage,writer,previousWriter='',clock=()=>Date.now(),onWrite=()=>{}){
    this.storage=storage;this.writer=writer;this.previousWriter=previousWriter;this.clock=clock;this.onWrite=onWrite;this.prefix='agy-draft-v2:';this.records=new Map();this.sequence=0;
  }
  entries(){
    const result=[],now=this.clock();
    for(let i=0;i<this.storage.length;i++){
      const key=this.storage.key(i);if(!key?.startsWith(this.prefix))continue;
      try{const value=JSON.parse(this.storage.getItem(key));
        if(typeof value.context==='string'&&value.context.length<=100&&typeof value.text==='string'&&value.text.length<=32000&&typeof value.writer==='string'&&Number.isFinite(value.at)&&now-value.at<=7*86400000&&value.at<=now+300000)result.push({...value,key});
      }catch(e){}
    }
    return result.sort((a,b)=>b.at-a.at||b.key.localeCompare(a.key));
  }
  read(context){
    const entries=this.entries().filter(r=>r.context===context),value=entries.find(r=>r.writer===this.previousWriter)||entries[0];
    if(value)this.records.set(context,value);return value?.text||'';
  }
  write(context,text){
    if(typeof context!=='string'||context.length>100||typeof text!=='string'||text.length>32000)return false;
    const previous=this.records.get(context);if(previous?.text===text)return true;
    const value={context,text,writer:this.writer,at:this.clock()},key=this.prefix+encodeURIComponent(context)+':'+this.writer+':'+(++this.sequence);
    this.storage.setItem(key,JSON.stringify(value));try{this.onWrite();}catch(e){}
    if(previous&&(previous.writer===this.writer||previous.writer===this.previousWriter))this.storage.removeItem(previous.key);
    this.records.set(context,{...value,key});this.prune();return true;
  }
  clearSent(context,text){
    const saved=this.records.get(context);if(!saved||saved.text!==text)return;
    const raw=this.storage.getItem(saved.key);
    if(raw){try{const value=JSON.parse(raw);if(value.text===text&&value.writer===saved.writer&&value.at===saved.at)this.storage.removeItem(saved.key);}catch(e){}}
    this.records.delete(context);
  }
  prune(){
    const valid=this.entries(),keep=new Set();let chars=0;
    for(const record of valid){if(keep.size>=64||chars+record.text.length>256000)continue;keep.add(record.key);chars+=record.text.length;}
    const remove=[];for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i);if(key?.startsWith(this.prefix)&&!keep.has(key))remove.push(key);}remove.forEach(key=>this.storage.removeItem(key));
  }
}
function makeDraftStore(){try{const previous=sessionStorage.getItem('agy-draft-writer')||'',writer=crypto.randomUUID();return new DraftStore(localStorage,writer,previous,()=>Date.now(),()=>sessionStorage.setItem('agy-draft-writer',writer));}catch(e){return null;}}
const draftStore=makeDraftStore();let draftSaveTimer=null,draftPending=null,draftRestored=false,draftStorageWarned=false,draftPersisted=true;
let collaborationPending=false;
let roundtablePending=false,roundtableHostsExpanded=false;
let multiAIExpanded=false,consoleStartupExpanded=false,consoleStartupData=null,consoleStartupPending=false,consoleStartupSaving=false,consoleAutoStartPending=false,consoleLauncherDirty=false,consoleStartupSequence=0;
const preferredModelKey='agy-global-model-v1';
let preferredModel='';try{preferredModel=localStorage.getItem(preferredModelKey)||'';}catch(e){}
let modelChoiceApplied=false;
let modelSaving=false,lastServerModelStamp=null,accountAnchor='header';
let quickTooltipTimer=null,quickTooltipTarget=null;
let collaborationAudit={cid:null,sequence:0,records:[],before:null,hasOlder:false,scanned:0,pending:false};
let queuePending=false,reloadBusy=false,lastServiceInstance=null,idlePolls=0;
let autoOpenPending=false;
const uiSettingsKey='agy-ui-settings-v1';
const themePresets={
  obsidian:{label:'黑曜',vars:{bg:'#0b0d10',side:'#080a0d',panel:'#151922',line:'#252a35',text:'#f0f2f7',muted:'#9aa3b5',accent:'#aeb8ff',green:'#82d0aa'}},
  light:{label:'白色',vars:{bg:'#ffffff',side:'#f5f6f8',panel:'#ffffff',line:'#d9dde5',text:'#1f2937',muted:'#687386',accent:'#4a5eb1',green:'#23754f'}},
  graphite:{label:'石墨',vars:{bg:'#202124',side:'#17181a',panel:'#2a2c30',line:'#3b3e44',text:'#f2f3f5',muted:'#a3a7af',accent:'#b8c7ff',green:'#8fd4b3'}},
  ocean:{label:'蓝灰',vars:{bg:'#121b27',side:'#0c141f',panel:'#1b293a',line:'#30445e',text:'#edf4ff',muted:'#9eafc6',accent:'#9fc5ff',green:'#82d6b8'}},
  warm:{label:'暖灰',vars:{bg:'#211f1f',side:'#171514',panel:'#302b2b',line:'#493e3d',text:'#faf1ed',muted:'#b9a8a2',accent:'#e3b8ad',green:'#9bcfb2'}}
};
const validThemes=new Set(Object.keys(themePresets));
function cleanModelOrder(value){return Array.isArray(value)?[...new Set(value.filter(id=>typeof id==='string'&&/^[a-z0-9][a-z0-9_.-]{0,99}$/.test(id)))].slice(0,128):[];}
function loadUISettings(){try{const value=JSON.parse(localStorage.getItem(uiSettingsKey)||'{}');if(value.theme==='night'){value.theme='light';try{localStorage.setItem(uiSettingsKey,JSON.stringify(value));}catch(e){}}return {font:[14,16,18].includes(value.font)?value.font:14,idleMs:[4000,12000].includes(value.idleMs)?value.idleMs:12000,theme:validThemes.has(value.theme)?value.theme:'light',modelOrder:cleanModelOrder(value.modelOrder)};}catch(e){return {font:14,idleMs:12000,theme:'light',modelOrder:[]};}}
let uiSettings=loadUISettings();
let modelOrderExpanded=false;
let settingsInsightsExpanded=false,settingsInsights={key:null,sequence:0,data:null,error:'',pending:false};
function applyUISettings(){const theme=themePresets[uiSettings.theme]||themePresets.light;for(const [name,value] of Object.entries(theme.vars))document.documentElement.style.setProperty('--'+name,value);if(document.documentElement.dataset)document.documentElement.dataset.theme=uiSettings.theme;document.documentElement.style.setProperty('--chat-font-size',uiSettings.font+'px');}
function saveUISetting(name,value){if(name==='font'&&[14,16,18].includes(value)||name==='idleMs'&&[4000,12000].includes(value)||name==='theme'&&validThemes.has(value))uiSettings[name]=value;else return;
  try{localStorage.setItem(uiSettingsKey,JSON.stringify(uiSettings));}catch(e){toast('已应用，但浏览器未能保存设置。');}applyUISettings();renderSettings();if(name==='idleMs')startPolling();}
function settingsOwner(){if(viewMode==='panel')return conversationMeta(activeId);const owner=fixedOwnerConversation();return requestedCodexThread&&owner?.codex_thread_id===requestedCodexThread?owner:null;}
function renderSettings(){
  renderSoftwareUpdates();
  if(!$('settings-dialog').open)return;
  const owner=settingsOwner();
  renderMultiAISettings(owner);
  renderConsoleStartupSettings(owner);
  renderRoundtableSettings(owner);
  const autoOpen=$('settings-auto-open'),autoSupported=snapshot.service?.features?.panel_auto_open===true;
  autoOpen.dataset.conversation=owner?.id||'';autoOpen.disabled=!owner?.codex_thread_id||!online||autoOpenPending||!autoSupported;
  autoOpen.setAttribute('aria-checked',String(owner?.auto_open_activity!==false));autoOpen.textContent=!autoSupported?'待更新':autoOpenPending?'保存中…':owner?.auto_open_activity===false?'关':'开';
  document.querySelectorAll('[data-font-size]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.fontSize)===uiSettings.font)));
  document.querySelectorAll('[data-idle-ms]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.idleMs)===uiSettings.idleMs)));
  document.querySelectorAll('[data-theme-choice]').forEach(b=>{if(b.dataset&&b.dataset.themeChoice)b.setAttribute('aria-pressed',String(b.dataset.themeChoice===uiSettings.theme));});
  renderGlobalModelSettings();
  $('settings-reload-state').textContent=reloadBusy?'正在保存并加载更新…':!snapshot.service?.features?.foreground_reload?'首次升级需重开原终端':snapshot.service.can_reload?'空闲，可在当前终端原地加载':'等待任务与账号检查结束';
  renderFallbackSettings();
  renderSettingsInsights();
  if(typeof renderPanelActions==='function')renderPanelActions();
}
function renderModelOrder(){
  if(!$('settings-model-order'))return; // Legacy saved order remains readable for migration.
  $('settings-model-order').hidden=!modelOrderExpanded;$('settings-model-order-toggle').setAttribute('aria-expanded',String(modelOrderExpanded));$('settings-model-order-toggle').textContent=modelOrderExpanded?'收起':'调整';
  $('settings-model-order-reset').disabled=!uiSettings.modelOrder.length||!!modelOrderSaving;
  if(!modelOrderExpanded)return;
  const groups=modelFamilies(orderedModels(snapshot.health?.models||[],uiSettings.modelOrder));
  const html=groups.map((group,index)=>`<div class="model-order-row"><span><b class="order-number">${index+1}</b>${escape(group.label)}</span><select data-model-order-tier="${escape(group.key)}" aria-label="${escape(group.label)} 默认候补档位" ${modelOrderSaving?'disabled':''}>${group.variants.map(m=>`<option value="${escape(m.id)}">${escape(modelEffortLabels[m.effort]||'默认档位')}</option>`).join('')}</select><button type="button" data-model-order-id="${escape(group.variants[0].id)}" data-model-order-move="up" aria-label="上移 ${escape(group.label)}" ${index===0||modelOrderSaving?'disabled':''}>↑</button><button type="button" data-model-order-id="${escape(group.variants[0].id)}" data-model-order-move="down" aria-label="下移 ${escape(group.label)}" ${index===groups.length-1||modelOrderSaving?'disabled':''}>↓</button></div>`).join('')||'<p>等待模型列表…</p>';
  if($('settings-model-order-list').innerHTML!==html)$('settings-model-order-list').innerHTML=html;
}
let modelOrderSaving=false;
async function syncBackendOrder(){
  if(!snapshot.service?.features?.quota_failover||modelOrderSaving)return;
  if(snapshot.model_order_version||!uiSettings.modelOrder.length){uiSettings.modelOrder=cleanModelOrder(snapshot.model_order);return;}
  modelOrderSaving=true;
  try{const result=await api('/api/model-order',{order:uiSettings.modelOrder,if_unset:true});Object.assign(snapshot,result);uiSettings.modelOrder=cleanModelOrder(result.model_order);}
  catch(e){toast('原有排序尚未同步：'+e.message);}
  finally{modelOrderSaving=false;}
}
async function saveModelOrder(order){
  if(modelOrderSaving)return;
  order=cleanModelOrder(order);
  if(snapshot.service?.features?.quota_failover){
    modelOrderSaving=true;
    try{const result=await api('/api/model-order',{order,version:snapshot.model_order_version||0});Object.assign(snapshot,result);order=result.model_order;}
    catch(e){toast('排序未保存：'+e.message);await refresh();return;}
    finally{modelOrderSaving=false;}
  }
  uiSettings.modelOrder=cleanModelOrder(order);try{localStorage.setItem(uiSettingsKey,JSON.stringify(uiSettings));}catch(e){toast('浏览器未能保存本地排序；后台已保存时仍可使用。');}render();
}

function moveModelOrder(id,direction){
  if(modelOrderSaving)return;
  const groups=modelFamilies(orderedModels(snapshot.health?.models||[],uiSettings.modelOrder)),index=groups.findIndex(g=>g.variants.some(m=>m.id===id)),target=index+(direction==='up'?-1:direction==='down'?1:0);
  if(index<0||target<0||target>=groups.length||target===index)return;
  [groups[index],groups[target]]=[groups[target],groups[index]];const ids=groups.flatMap(g=>g.variants.map(m=>m.id)),known=new Set(ids),scroll=$('settings-model-order-list').scrollTop;
  saveModelOrder([...ids,...uiSettings.modelOrder.filter(key=>!known.has(key))]);$('settings-model-order-list').scrollTop=scroll;
}
let fallbackExpanded=false,fallbackSaving=false,fallbackScopeId='',fallbackAvailableExpanded=false;
function fallbackView(){
  const owner=settingsOwner()||conversationMeta(fallbackScopeId),kind=$('fallback-kind').value==='image'?'image':'text';
  return {owner,kind,settings:owner?.fallback_preferences?.[kind]||{enabled:true}};
}
function fallbackCandidates(owner,kind,settings){
  if(Array.isArray(settings.models))return settings.models;
  const models=orderedModels(snapshot.health?.models||[],uiSettings.modelOrder);
  if(kind==='text')return modelFamilies(models).map(g=>g.variants[0].id);
  const selected=owner?.model_preferences?.image||snapshot.preferred_model;
  return [...new Set([selected,...(snapshot.verified_image_models||[])].filter(Boolean))];
}
function fallbackModelGroups(owner,kind,settings){
  const models=orderedModels(snapshot.health?.models||[],uiSettings.modelOrder),candidates=fallbackCandidates(owner,kind,settings);
  return modelFamilies(orderedModels(models,[...new Set([...candidates,...models.map(m=>m.id)])]));
}
function moveFallbackModel(family,direction){
  if(fallbackSaving)return;
  const {owner,kind,settings}=fallbackView();if(!owner)return;
  const groups=fallbackModelGroups(owner,kind,settings),candidates=fallbackCandidates(owner,kind,settings);
  const selected=groups.map(g=>g.variants.find(m=>candidates.includes(m.id))?.id).filter(Boolean);
  const index=selected.findIndex(id=>groups.find(g=>g.key===family)?.variants.some(m=>m.id===id)),target=index+(direction==='up'?-1:direction==='down'?1:0);
  if(index<0||target<0||target>=selected.length||target===index)return;
  [selected[index],selected[target]]=[selected[target],selected[index]];
  return saveFallbackSettings({...settings,models:selected});
}
function renderFallbackSettings(){
  const panel=$('fallback-panel');if(!panel)return;
  const {owner,kind,settings}=fallbackView(),supported=snapshot.service?.features?.quota_failover===true;
  const fixed=settingsOwner(),scope=$('fallback-scope');
  $('fallback-expand').disabled=false;
  $('fallback-scope-row').hidden=!!fixed;
  const options='<option value="">选择要配置的聊天</option>'+(snapshot.conversations||[]).filter(c=>!c.deleted_at).map(c=>'<option value="'+escape(c.id)+'">'+escape(c.title||'未命名聊天')+'</option>').join('');
  if(scope.innerHTML!==options)scope.innerHTML=options;scope.value=fixed?.id||owner?.id||'';scope.disabled=fallbackSaving;
  panel.hidden=!fallbackExpanded;$('fallback-expand').setAttribute('aria-expanded',String(fallbackExpanded));
  $('fallback-expand').textContent=fallbackExpanded?'收起':'配置';
  $('fallback-summary').textContent=!supported?'加载新版后台后可用':!owner?'按聊天配置 · 文字与生图独立':(settings.enabled?'已开启':'已关闭')+' · '+(kind==='text'?'文字':'生图')+' · '+(Array.isArray(settings.models)?'自选候补':'默认候补');
  $('fallback-status-chip').textContent=!supported?'需更新':!owner?'未选择聊天':settings.enabled?'已开启':'已关闭';
  if(typeof document!=='undefined')document.querySelectorAll('[data-fallback-kind]').forEach(button=>{if(button.dataset&&button.dataset.fallbackKind)button.setAttribute('aria-pressed',String(button.dataset.fallbackKind===kind));});
  if(!fallbackExpanded)return;
  const editable=!!owner&&supported&&online&&!fallbackSaving;
  const button=$('fallback-enabled');button.disabled=!editable;button.setAttribute('aria-checked',String(!!owner&&settings.enabled));button.textContent=fallbackSaving?'保存中…':!owner?'—':settings.enabled?'开':'关';
  $('fallback-kind').disabled=fallbackSaving;$('fallback-reset').disabled=!editable;
  if(!owner||!supported){$('fallback-note').textContent=!supported?'当前后台不支持保存候补，请加载新版后台。':'先选择聊天，再设置文字或生图候补。';$('fallback-models').innerHTML='';$('fallback-available').innerHTML='';$('fallback-add-toggle').hidden=true;$('fallback-status').textContent='尚未修改任何配置';return;}
  const candidates=fallbackCandidates(owner,kind,settings),groups=fallbackModelGroups(owner,kind,settings);
  const count=groups.filter(g=>g.variants.some(m=>candidates.includes(m.id))).length;
  $('fallback-note').textContent=kind==='image'?'仅勾选有生图能力的模型；额度用完后按编号接替。':'当前模型可用时继续使用；额度用完后按编号接替。';
  let candidateIndex=0;const selectedRows=[];const availableRows=[];
  groups.forEach(g=>{
    const chosen=g.variants.find(m=>candidates.includes(m.id));
    const number=chosen?++candidateIndex:null;
    const row='<div class="fallback-model-row '+(chosen?'is-selected':'is-available')+'"><label class="fallback-model-label"><b class="order-number">'+(number||'—')+'</b><input type="checkbox" data-fallback-family="'+escape(g.key)+'" '+(chosen?'checked ':'')+(!editable?'disabled ':'')+'><span>'+escape(g.label)+'</span></label><select aria-label="'+escape(g.label)+' 候补档位" data-fallback-tier="'+escape(g.key)+'" '+(!chosen||!editable?'disabled':'')+'>'+g.variants.map(m=>'<option value="'+escape(m.id)+'"'+(m.id===chosen?.id?' selected':'')+'>'+escape(modelEffortLabels[m.effort]||'默认档位')+'</option>').join('')+'</select><div class="fallback-arrows"><button type="button" data-fallback-move="up" data-fallback-id="'+escape(g.key)+'" aria-label="上移 '+escape(g.label)+'" '+(!number||number===1||!editable?'disabled':'')+'>↑</button><button type="button" data-fallback-move="down" data-fallback-id="'+escape(g.key)+'" aria-label="下移 '+escape(g.label)+'" '+(!number||number===count||!editable?'disabled':'')+'>↓</button></div></div>';
    (chosen?selectedRows:availableRows).push(row);
  });
  const selectedHtml=selectedRows.length?'<div class="fallback-list-label">已启用候补 · ↑↓ 调整顺序</div>'+selectedRows.join(''):'<p class="fallback-empty">还没有候补模型，请从下方添加。</p>';
  const availableHtml=availableRows.length?'<div class="fallback-list-label fallback-list-label-muted">可选模型</div>'+availableRows.join(''):'<p class="fallback-empty">没有更多可选模型。</p>';
  if($('fallback-models').innerHTML!==selectedHtml)$('fallback-models').innerHTML=selectedHtml;
  if($('fallback-available').innerHTML!==availableHtml)$('fallback-available').innerHTML=availableHtml;
  $('fallback-add-toggle').hidden=!availableRows.length;$('fallback-add-toggle').setAttribute('aria-expanded',String(fallbackAvailableExpanded));$('fallback-add-toggle').textContent=fallbackAvailableExpanded?'收起可选模型':'＋ 添加模型';$('fallback-available').hidden=!fallbackAvailableExpanded;
  $('fallback-status').textContent=(!online?'服务已断开 · 仅查看':fallbackSaving?'保存中…':candidateIndex+' 个候补 · '+(Array.isArray(settings.models)?'已保存顺序':'默认顺序'))+' · '+(kind==='text'?'文字':'生图');
}
async function saveFallbackSettings(value){
  if(fallbackSaving)return;
  const {owner,kind}=fallbackView();if(!owner)return;
  if(!online||!snapshot.service?.features?.quota_failover)return;
  const cid=owner.id;fallbackSaving=true;renderFallbackSettings();
  try{const saved=await api('/api/conversations/'+cid+'/fallback',{kind,settings:value});
    if(saved.id!==cid)throw Error('窗口不匹配');
    const current=conversationMeta(cid);if(current)Object.assign(current,saved);
    const cached=conversationPages.get(pageCacheKey(cid));if(cached?.value)Object.assign(cached.value,saved);
  }catch(e){toast('候补设置未保存：'+e.message);}
  finally{fallbackSaving=false;render();}
}
function installLightDismiss(dialog){
  let beganOutside=false;
  const outside=event=>{const r=dialog.getBoundingClientRect();return event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom;};
  dialog.addEventListener('pointerdown',event=>{beganOutside=event.button===0&&event.target===dialog&&outside(event);});
  dialog.addEventListener('pointerup',event=>{const dismiss=beganOutside&&event.target===dialog&&outside(event);beganOutside=false;if(dismiss)dialog.close();});
  dialog.addEventListener('pointercancel',()=>{beganOutside=false;});dialog.addEventListener('close',()=>{beganOutside=false;});
}

function settingsInsightsScope(){
  const conv=viewMode==='panel'?conversationMeta(activeId):null;
  return conv?{cid:conv.id,branch:selectedBranch(conv)||conv.primary_branch||'main',title:conv.branches?.[selectedBranch(conv)||conv.primary_branch]?.title||'当前会话'}:null;
}
function settingsInsightsKey(scope){return scope?scope.cid+':'+(scope.branch||''):'overview';}
function renderSettingsInsights(){
  if(!$('settings-dialog').open)return;
  const scope=settingsInsightsScope(),key=settingsInsightsKey(scope),data=settingsInsights.key===key?settingsInsights.data:null;
  $('settings-insights-toggle').textContent=settingsInsightsExpanded?'收起':'查看';
  $('settings-insights-toggle').setAttribute('aria-expanded',String(settingsInsightsExpanded));$('settings-insights').hidden=!settingsInsightsExpanded;
  $('settings-insights-summary').textContent=data?data.session_title+' · 面板记录 '+data.turn_count+' 轮':'上下文、压缩记录与运行内存';
  if(!settingsInsightsExpanded)return;
  const pending=settingsInsights.pending&&settingsInsights.key===key;
  $('settings-insights-refresh').disabled=pending||resourcePending||!online;
  $('settings-insights-refresh').textContent=pending||resourcePending?'读取中…':'刷新';
  let context='';
  if(!scope)context='<p class="settings-metric-note">进入聊天后可查看对应上下文记录。</p>';
  else if(settingsInsights.key===key&&settingsInsights.error)context='<p class="settings-metric-note" role="status">'+escape(settingsInsights.error)+'</p>';
  else if(!data)context='<p class="settings-metric-note">'+(pending?'正在读取本会话…':'点击刷新读取本会话记录。')+'</p>';
  else{
    const current=active(),visible=current?.id===scope.cid&&(current.page?.branch||selectedBranch(conversationMeta(current.id)))===scope.branch?(current.turns||[]).length:null;
    const compact=data.manual_compaction,usage=data.latest_usage;
    context='<h3>聊天上下文 · '+escape(data.session_title)+'</h3><dl class="settings-metrics"><div><dt>面板记录</dt><dd>'+escape(data.turn_count)+' 轮'+(visible!==null?' · 当前页 '+visible+' 轮':'')+'</dd></div><div><dt>压缩指令</dt><dd>'+(compact?escape(accountCheckedLabel(compact.completed_at))+(compact.checkpoint_verified?' · 检查点已核验':' · 指令已完成，检查点未核验')+' · 此后 '+escape(compact.turns_after)+' 轮':'未找到 /compact 完成记录')+'</dd></div><div><dt>最近用量</dt><dd>'+(usage?escape(tokenUsageText(usage))+'<small>'+escape(accountCheckedLabel(usage.recorded_at))+' · '+escape(texts[usage.status]||usage.status)+'</small>':'暂未取得')+'</dd></div></dl><p class="settings-metric-note">自动压缩次数和实时上下文比例暂未提供。</p>';
  }
  $('settings-context-body').innerHTML=context;
  let memory='<h3>运行内存 · 所有窗口共用</h3>';
  if(resourcePending&&!resourceData)memory+='<p class="settings-metric-note">正在读取进程占用…</p>';
  else if(resourceError)memory+='<p class="settings-metric-note" role="status">'+escape(resourceError)+'</p>';
  else if(!resourceData)memory+='<p class="settings-metric-note">点击刷新读取进程占用。</p>';
  else{
    memory+='<dl class="settings-metrics">'+(resourceData.processes||[]).map(p=>{const physical=typeof p.footprint_mb==='number'&&Number.isFinite(p.footprint_mb),rss=typeof p.rss_mb==='number'&&Number.isFinite(p.rss_mb),value=physical?p.footprint_mb:rss?p.rss_mb:null;return '<div><dt>'+escape(String(p.role).replace(/\bAg\b/g,'Agy'))+'</dt><dd>'+(value===null?'未取得':value.toFixed(1)+' MB · '+(physical?'物理内存':'RSS'))+'</dd></div>';}).join('')+'</dl>';
    if(!(resourceData.processes||[]).length)memory+='<p class="settings-metric-note">暂未取得进程列表。</p>';
    if(resourceData.warning)memory+='<p class="settings-metric-note">'+escape(resourceData.warning)+'</p>';
    if(resourceData.omitted)memory+='<p class="settings-metric-note">另有 '+escape(resourceData.omitted)+' 个子进程未展开。</p>';
    memory+='<p class="settings-metric-note">'+escape(accountCheckedLabel(resourceData.checked_at))+' 读取</p>';
  }
  $('settings-memory-body').innerHTML=memory+'<p class="settings-metric-note">浏览器标签页的独占内存暂无法单独读取。</p>';
}
async function readSettingsInsights(force=false){
  const scope=settingsInsightsScope(),key=settingsInsightsKey(scope),sequence=settingsInsights.sequence+1;
  settingsInsights={key,sequence,data:null,error:'',pending:!!scope};renderSettingsInsights();
  const context=scope?(async()=>{
    try{
      const data=await api('/api/conversations/'+scope.cid+'/context?'+new URLSearchParams(scope.branch?{branch:scope.branch}:{}));
      if(data.conversation_id!==scope.cid||data.branch!==scope.branch)throw new Error('返回的会话不匹配，请刷新重试。');
      if(sequence===settingsInsights.sequence&&key===settingsInsightsKey(settingsInsightsScope())&&$('settings-dialog').open)settingsInsights.data=data;
    }catch(error){if(sequence===settingsInsights.sequence)settingsInsights.error=error.status===404?'上下文详情需加载新版后台；可用设置中的“重载更新”。':error.message;}
    finally{if(sequence===settingsInsights.sequence){settingsInsights.pending=false;renderSettingsInsights();}}
  })():Promise.resolve();
  await Promise.allSettled([context,readResources(force)]);
}

function hideQuickTooltip(){
  clearTimeout(quickTooltipTimer);quickTooltipTimer=null;
  if(quickTooltipTarget){const ids=(quickTooltipTarget.getAttribute('aria-describedby')||'').split(/\s+/).filter(id=>id&&id!=='quick-tooltip');if(ids.length)quickTooltipTarget.setAttribute('aria-describedby',ids.join(' '));else quickTooltipTarget.removeAttribute('aria-describedby');}
  quickTooltipTarget=null;const box=$('quick-tooltip');if(box.matches?.(':popover-open'))box.hidePopover();box.hidden=true;
}
function paintQuickTooltip(){
  const target=quickTooltipTarget,box=$('quick-tooltip');if(!target||!target.isConnected||document.hidden)return hideQuickTooltip();
  const text=target.dataset.tooltip||'';if(!text)return hideQuickTooltip();box.textContent=text;box.hidden=false;
  if(box.showPopover&&!box.matches(':popover-open'))box.showPopover();
  const rect=target.getBoundingClientRect(),width=box.offsetWidth,height=box.offsetHeight;
  box.style.left=Math.max(8,Math.min(rect.left+(rect.width-width)/2,window.innerWidth-width-8))+'px';
  box.style.top=(rect.top-height-9>=8?rect.top-height-9:Math.min(window.innerHeight-height-8,rect.bottom+9))+'px';
  const ids=new Set((target.getAttribute('aria-describedby')||'').split(/\s+/).filter(Boolean));ids.add('quick-tooltip');target.setAttribute('aria-describedby',[...ids].join(' '));
}
function scheduleQuickTooltip(target){
  if(quickTooltipTarget===target)return;hideQuickTooltip();quickTooltipTarget=target;
  quickTooltipTimer=setTimeout(()=>{quickTooltipTimer=null;if(openSelectId)return hideQuickTooltip();paintQuickTooltip();},300);
}

function quotaGroupsForModel(model){
  if(/^(claude-|gpt-)/.test(model))return ['3p-5h','3p-weekly'];
  if(model.startsWith('gemini-'))return ['gemini-5h','gemini-weekly',...(/argon|orchestrator/.test(model)?['gemini-argon-wtu-weekly']:[])];
  return [];
}
function quotaForModel(model,health){
  const account=health.account||{},groups=quotaGroupsForModel(model),quotas=account.quota||{};
  const values=account.state==='known'||account.state==='checking'?groups.map(k=>quotas[k]).filter(b=>!b?.reset_time||!Number.isFinite(Date.parse(b.reset_time))||Date.parse(b.reset_time)>Date.now()).map(b=>b?.remaining_fraction).filter(x=>typeof x==='number'&&Number.isFinite(x)&&x>=0&&x<=1):[];
  const exhausted=values.includes(0),fraction=exhausted?0:groups.length&&values.length===groups.length?Math.min(...values):null;
  return {fraction,exhausted,checked_at:account.checked_at,bucket:fraction===null?'':groups.find(k=>quotas[k]?.remaining_fraction===fraction)||''};
}
function availableQuotaModel(health){
  const models=health.models||[],order=[...(snapshot.effective_model_order||snapshot.model_order||[]),'gemini-4-argon-high','gemini-3.8-flash-high','claude-sonnet-5-5-medium','gemini-4-hydrogen-high','gemini-3.8-flash-medium','claude-sonnet-5-5-high',...models.map(m=>m.id)];
  return order.find(id=>models.some(m=>m.id===id)&&!(snapshot.blocked_models||[]).includes(id)&&!quotaForModel(id,health).exhausted)||null;
}
function orderedModels(models,order=[]){if(!order.length)order=snapshot.effective_model_order||[];const rank=new Map(order.map((id,i)=>[id,i]));return models.map((m,i)=>({m,i})).sort((a,b)=>(rank.get(a.m.id)??Infinity)-(rank.get(b.m.id)??Infinity)||a.i-b.i).map(row=>row.m);}
const modelEffortMemoryKey='agy-model-efforts-v1';
let globalEffortDraft=null;
function readModelEfforts(){
  try{const rows=JSON.parse(localStorage.getItem(modelEffortMemoryKey)||'[]');return Array.isArray(rows)?rows.filter(r=>r&&typeof r.scope==='string'&&r.scope.length<=160&&typeof r.family==='string'&&typeof r.model==='string'&&/^[a-z0-9][a-z0-9_.-]{0,99}$/.test(r.model)).slice(0,256):[];}catch(e){return [];}
}
function modelEffortScope(sourceId){
  if(sourceId==='global-model-select'||!snapshot.service?.features?.window_models)return 'global';
  const conv=composerConversation();return conv?JSON.stringify([conv.id,currentModelKind()]):null;
}
function rememberModelEfforts(scope,models){
  if(!scope)return;
  let rows=readModelEfforts(),changed=false;
  for(const id of models){const model=(snapshot.health?.models||[]).find(m=>m.id===id);if(!model)continue;const family=modelParts(model).key;rows=rows.filter(r=>r.scope!==scope||r.family!==family);rows.unshift({scope,family,model:id});changed=true;}
  if(!changed)return;
  try{localStorage.setItem(modelEffortMemoryKey,JSON.stringify(rows.slice(0,256)));}catch(e){}
}
function rememberedModelEffort(sourceId,family){
  if(sourceId==='global-model-select'&&globalEffortDraft?.has(family))return globalEffortDraft.get(family);
  const scope=modelEffortScope(sourceId);return scope?readModelEfforts().find(r=>r.scope===scope&&r.family===family)?.model:null;
}
function stageGlobalEffort(model){
  const item=(snapshot.health?.models||[]).find(m=>m.id===model);if(!item)return;
  if(!globalEffortDraft)globalEffortDraft=new Map();globalEffortDraft.set(modelParts(item).key,model);
}
function scopedModelValue(conv,kind){return Object.hasOwn(conv?.model_preferences||{},kind)?conv.model_preferences[kind]:'__global__';}
function currentModelKind(){return $('task-kind').value==='image'?'image':'text';}
function composerModelResolution(){
  if(!snapshot.service?.features?.model_resolution)return null;
  const conv=typeof composerConversation==='function'?composerConversation():null,kind=currentModelKind(),value=conv?conv.model_resolution?.[kind]:snapshot.model_resolution?.[kind];
  const preferences=conv?.model_preferences||{},wanted=Object.hasOwn(preferences,kind)?preferences[kind]:snapshot.preferred_model||'';
  const effective=value?.effective_model;
  if(value?.automatic&&effective&&((snapshot.blocked_models||[]).includes(effective)||quotaForModel(effective,snapshot.health||{}).exhausted))return null;
  return value&&value.requested_model===(wanted||'')?value:null;
}
function effectiveComposerModel(health){const resolution=composerModelResolution();if(resolution)return resolution.effective_model||resolution.requested_model;const selected=$('model').value;return selected==='__global__'?(snapshot.preferred_model||availableQuotaModel(health)||''):selected||availableQuotaModel(health)||'';}
const modelEffortLabels={minimal:'Minimal · 轻量',low:'Low · 轻量',medium:'Medium · 均衡',high:'High · 深入',xhigh:'XHigh · 更深入',max:'Max · 最深入'};
function modelParts(model){
  const match=String(model.label||'').match(/^(.*?)\s*\((Minimal|Low|Medium|High|XHigh|Max)\)$/i);
  const suffix=String(model.id).match(/^(.*)-(minimal|low|medium|high|xhigh|max)$/i);
  const effort=(match?.[2]||suffix?.[2]||'').toLowerCase();
  return {key:effort&&suffix?suffix[1]:model.id,label:match?match[1]:model.label||model.id,effort};
}
function modelFamilies(models){
  const groups=new Map();for(const model of models){const part=modelParts(model);if(!groups.has(part.key))groups.set(part.key,{key:part.key,label:part.label,variants:[]});groups.get(part.key).variants.push({...model,effort:part.effort});}return [...groups.values()];
}
function splitModelSelection(sourceId,familyId,speedId){
  const source=$(sourceId),family=$(familyId),speed=$(speedId);if(!source||!family||!speed)return;
  const models=orderedModels(snapshot.health?.models||[],uiSettings.modelOrder),groups=modelFamilies(models),selected=source.value;
  const group=groups.find(g=>g.variants.some(m=>m.id===selected));
  const specials=[...source.options].filter(o=>!models.some(m=>m.id===o.value)).map(o=>({value:o.value,label:o.value==='__global__'?'沿用全局':o.value===''?'智能选择':o.text,disabled:o.disabled}));
  const available=id=>[...source.options].some(o=>o.value===id&&!o.disabled);
  const options=[...specials,...groups.map(g=>({value:'family:'+g.key,label:g.label,disabled:!g.variants.some(m=>available(m.id))}))];
  const html=options.map(o=>`<option value="${escape(o.value)}"${o.disabled?' disabled':''}>${escape(o.label)}</option>`).join('');
  if(family.innerHTML!==html)family.innerHTML=html;family.value=group?'family:'+group.key:selected;family.disabled=source.disabled;
  const rank=['minimal','low','medium','high','xhigh','max'];
  const variants=group?[...group.variants].sort((a,b)=>rank.indexOf(a.effort)-rank.indexOf(b.effort)):[];
  const speedHTML=variants.length?variants.map(m=>`<option value="${escape(m.id)}"${available(m.id)?'':' disabled'}>${escape(modelEffortLabels[m.effort]||'默认档位')}</option>`).join(''):'<option value="">'+(selected==='__global__'||selected===''?'自动档位':'不可用')+'</option>';
  if(speed.innerHTML!==speedHTML)speed.innerHTML=speedHTML;speed.value=group?selected:'';speed.disabled=source.disabled||variants.length<2;
  family.dataset.source=sourceId;speed.dataset.source=sourceId;
}
function resetSplitModel(sourceId){splitModelSelection(sourceId,sourceId==='model'?'model-family':'global-model-family',sourceId==='model'?'model-speed':'global-model-speed');}
function chooseModelFamily(sourceId,familyId){
  const source=$(sourceId),family=$(familyId);if(source.disabled||family.disabled)return resetSplitModel(sourceId);
  const groups=modelFamilies(orderedModels(snapshot.health?.models||[],uiSettings.modelOrder));
  const group=groups.find(g=>'family:'+g.key===family.value);let chosen=family.value;
  if(group){const variants=group.variants.filter(m=>[...source.options].some(o=>o.value===m.id&&!o.disabled)),previous=(snapshot.health?.models||[]).find(m=>m.id===source.value),effort=previous?modelParts(previous).effort:'',remembered=rememberedModelEffort(sourceId,group.key);chosen=(variants.find(m=>m.id===remembered)||variants.find(m=>m.effort===effort)||variants[0])?.id;}
  if(![...source.options].some(o=>o.value===chosen&&!o.disabled))return resetSplitModel(sourceId);
  if(sourceId==='global-model-select'){stageGlobalEffort(source.value);stageGlobalEffort(chosen);}
  source.value=chosen;if(sourceId==='model')source.dispatchEvent(new Event('change',{bubbles:true}));else splitModelSelection(sourceId,familyId,'global-model-speed');
}
function chooseModelSpeed(sourceId,speedId){
  const source=$(sourceId),speed=$(speedId);if(source.disabled||speed.disabled)return resetSplitModel(sourceId);
  if(![...source.options].some(o=>o.value===speed.value&&!o.disabled))return resetSplitModel(sourceId);
  if(sourceId==='global-model-select')stageGlobalEffort(speed.value);
  source.value=speed.value;if(sourceId==='model')source.dispatchEvent(new Event('change',{bubbles:true}));
}
function syncComposerModelPickers(){splitModelSelection('model','model-family','model-speed');}
function renderModelSelection(health,conv){
  if(snapshot.service?.features?.window_models){
    const target=typeof composerConversation==='function'?composerConversation():conv,kind=currentModelKind(),wanted=scopedModelValue(target,kind),models=orderedModels(health.models||[],uiSettings.modelOrder);
    const globalLabel=models.find(m=>m.id===snapshot.preferred_model)?.label||snapshot.preferred_model||'智能选择';
    const options=[{id:'__global__',label:'沿用全局 · '+globalLabel},{id:'',label:'本窗口智能选择'},...models];
    if(wanted&&wanted!=='__global__'&&!models.some(m=>m.id===wanted))options.push({id:wanted,label:wanted+'（暂不可用）',disabled:true});
    const html=options.map(m=>`<option value="${escape(m.id)}"${m.disabled?' disabled':''}>${escape(m.label)}</option>`).join('');
    if($('model').dataset.options!==html){$('model').innerHTML=html;$('model').dataset.options=html;}
    $('model').value=wanted;$('model').setAttribute('aria-label','本窗口'+(kind==='image'?'生图':'文字')+'模型');
    const trigger=$('model-family-trigger');if(trigger)trigger.setAttribute('aria-label','本窗口'+(kind==='image'?'生图':'文字')+'模型');
    return;
  }
  const supported=snapshot.service?.features?.global_model===true,stamp=snapshot.preferred_model_updated_at;
  if(supported&&stamp&&stamp!==lastServerModelStamp&&!modelSaving){preferredModel=snapshot.preferred_model||'';lastServerModelStamp=stamp;modelChoiceApplied=false;}
  const models=orderedModels(health.models||[],uiSettings.modelOrder),options=[...(supported?[{id:'',label:'智能选择（按额度）'}]:[]),...models];
  if(preferredModel&&!models.some(m=>m.id===preferredModel))options.push({id:preferredModel,label:preferredModel+(health.checking?'（等待检查）':'（暂不可用）'),disabled:true});
  const html=options.map(m=>`<option value="${escape(m.id)}"${m.disabled?' disabled':''}>${escape(m.label)}</option>`).join('');
  const old=$('model').value,changed=$('model').dataset.options!==html;
  if(changed){$('model').innerHTML=html;$('model').dataset.options=html;}
  if(changed||!modelChoiceApplied){
    const wanted=supported&&stamp?preferredModel:preferredModel||(supported?'':availableQuotaModel(health)||old);
    $('model').value=options.some(m=>m.id===wanted)?wanted:supported?'':models[0]?.id||'';
    if(options.some(m=>m.id===wanted))modelChoiceApplied=true;
  }
}
async function persistModelChoice(model=$('model').value){
  if(modelSaving)return;
  if(snapshot.service?.features?.window_models){
    let conv=composerConversation();const kind=currentModelKind(),selected=model==='__global__'?null:model;
    modelSaving=true;render();
    try{
      if(!conv){conv=await api('/api/conversations',{});snapshot.conversations.push(conv);selectConversation(conv.id);}
      const cid=conv.id,saved=await api('/api/conversations/'+cid+'/model',{kind,model:selected});
      if(saved.id!==cid)throw new Error('保存返回的窗口不匹配');
      const current=conversationMeta(cid);if(current)Object.assign(current,saved);
      const cached=conversationPages.get(pageCacheKey(cid));if(cached?.value)Object.assign(cached.value,saved);
      if(selected&&saved.model_preferences?.[kind]===selected)rememberModelEfforts(JSON.stringify([cid,kind]),[selected]);
      toast('已保存本窗口'+(kind==='image'?'生图':'文字')+'模型');
    }catch(error){toast('窗口模型未保存：'+error.message);}
    finally{modelSaving=false;render();}return;
  }
  const previous=preferredModel;preferredModel=model;modelChoiceApplied=true;
  if(snapshot.service?.features?.global_model===true){
    modelSaving=true;
    let failure=false;
    try{const saved=await api('/api/model',{model});Object.assign(snapshot,saved);lastServerModelStamp=saved.preferred_model_updated_at;}
    catch(e){preferredModel=previous;modelChoiceApplied=false;failure=true;toast('全局模型未保存：'+e.message);}
    finally{modelSaving=false;}
    if(failure){render();return;}
  }
  try{localStorage.setItem(preferredModelKey,preferredModel);}catch(e){if(!snapshot.service?.features?.global_model)toast('本页已切换，但浏览器无法保存选择。');}
  rememberModelEfforts('global',[preferredModel]);
  render();
}
function renderModelQuota(health){
  const resolution=composerModelResolution(),selected=$('model').value,model=effectiveComposerModel(health),q=quotaForModel(model,health),percent=q.fraction===null?null:Math.round(q.fraction*1000)/10;
  const label=(health.models||[]).find(m=>m.id===model)?.label||'当前默认模型',ring=$('quota-ring');
  $('quota-ring-value').textContent=percent===null?'?':Math.round(percent)+'%';
  ring.className='quota-ring'+(percent===null?' unknown':percent<=20?' low':'');
  $('quota-ring-fill').setAttribute('stroke-dasharray',(percent??0)+' 100');
  ring.removeAttribute('title');
  const blocked=(snapshot.blocked_models||[]).includes(model);
  ring.dataset.tooltip=blocked?'该模型最近被 CLI 拒绝；共享额度有余额也不能证明它可用':percent===null?'共享额度读取中或暂未提供':`共享${q.bucket.endsWith('5h')?'5 小时':'每周'}额度 · 剩余 ${percent}% · 具体模型以实际调用为准`;
  if(blocked){$('quota-ring-value').textContent='!';ring.className='quota-ring low';$('quota-ring-fill').setAttribute('stroke-dasharray','0 100');}
  const picker=$('model-family-trigger');if(picker){picker.removeAttribute('title');picker.dataset.tooltip=snapshot.service?.features?.window_models?'本窗口'+(currentModelKind()==='image'?'生图':'文字')+'：'+label+(selected==='__global__'?' · 沿用全局':selected?'':' · 智能选择'):selected?'全局默认：'+label:'全局默认：智能选择'+(model?' · 预计 '+label:'');}
  ring.setAttribute('aria-label',blocked?'模型最近被拒绝，点击查看额度':`模型共享额度：${percent===null?'未知':percent+'%'}，点击查看`);
  const alternative=(health.models||[]).find(m=>m.id===availableQuotaModel(health)),unavailable=!!model&&!(health.models||[]).some(m=>m.id===model),warn=resolution?resolution.automatic||!!resolution.error||q.exhausted: q.exhausted||!model||unavailable;
  $('model-warning').hidden=!warn;
  $('model-warning').classList.toggle('automatic',!!resolution?.automatic);
  $('model-quota-text').textContent=resolution?.error?resolution.error:resolution?.automatic?'将按排序自动使用 '+label:unavailable?'所选模型当前不可用':q.exhausted?'所选模型额度已耗尽'+(resolution?.fallback_enabled===false?' · 自动候补已关闭':''):'暂无已知可用额度';
  $('model-quota-text').dataset.tooltip=resolution?.automatic?'发送时自动接替，无需点击换模；执行前会重新核对额度。保存的首选模型保持不变。最近读取：'+(resolution.checked_at||'时间未知'):'';
  // New servers are authoritative for kind/allowlist; never suggest an unrelated text model for image work.
  const button=$('model-quota-alternative');button.hidden=!!snapshot.service?.features?.model_resolution||!!resolution||!alternative||alternative.id===model;
  button.dataset.model=alternative?.id||'';button.textContent=alternative?'改用 '+alternative.label:'';button.disabled=busy||submitting||modelSaving;
  // Picker label and menu checkmark always describe the same saved selection.
  // The forecast is shown above; it never rewrites just the visible label.
}



function closeSelectMenu(focus=false){const picker=selectPickers.get(openSelectId);openSelectId=null;$('select-menu').hidden=true;for(const item of selectPickers.values())item.button.setAttribute('aria-expanded','false');if(focus)picker?.button.focus();}
function renderSelectOptions(){
  const picker=selectPickers.get(openSelectId);if(!picker)return;
  const options=[...picker.select.options],fingerprint=JSON.stringify([openSelectId,picker.select.value,options.map(o=>[o.value,o.text,o.disabled])]);
  if(fingerprint===selectMenuRender)return;
  const focused=document.activeElement?.dataset.selectValue;
  $('select-menu').innerHTML=options.map(o=>`<button type="button" role="option" data-select-value="${escape(o.value)}" aria-selected="${o.value===picker.select.value}" ${o.disabled?'disabled':''}><span>${escape(o.text)}</span><span class="select-check" aria-hidden="true">${o.value===picker.select.value?'✓':''}</span></button>`).join('');
  selectMenuRender=fingerprint;
  if(focused!==undefined)[...$('select-menu').children].find(button=>button.dataset.selectValue===focused)?.focus();
}
function syncSelectPickers(){
  for(const picker of selectPickers.values()){picker.label.textContent=picker.select.selectedOptions[0]?.text||'请选择';picker.button.disabled=picker.select.disabled;if(picker.select.dataset.tooltip)picker.button.dataset.tooltip=picker.select.dataset.tooltip;if(picker.button.disabled&&openSelectId===picker.select.id)closeSelectMenu();}
  if(openSelectId)renderSelectOptions();
}
function openSelectMenu(id){
  const picker=selectPickers.get(id);if(!picker||picker.select.disabled)return;
  if(openSelectId===id){closeSelectMenu(true);return;}
  closeHistoryMenu();closeSelectMenu();openSelectId=id;selectMenuRender='';renderSelectOptions();
  const menu=$('select-menu'),box=picker.button.getBoundingClientRect();
  menu.setAttribute('aria-label',picker.select.getAttribute('aria-label')||'选择');menu.hidden=false;
  menu.style.minWidth=Math.min(window.innerWidth-16,Math.max(box.width,id==='model-family'?210:155))+'px';
  menu.style.maxHeight=Math.min(320,window.innerHeight-24)+'px';
  menu.style.left=Math.max(8,Math.min(box.left,window.innerWidth-menu.offsetWidth-8))+'px';
  menu.style.top=(box.bottom+menu.offsetHeight+6<=window.innerHeight-8?box.bottom+6:Math.max(8,box.top-menu.offsetHeight-6))+'px';
  picker.button.setAttribute('aria-expanded','true');const option=menu.querySelector('[aria-selected="true"]')||menu.querySelector('button:not(:disabled)');option?.focus();option?.scrollIntoView({block:'nearest'});
}
function setupSelectPickers(){
  for(const id of ['history-filter','model-family','model-speed','task-kind']){
    const select=$(id),wrap=document.createElement('div'),button=document.createElement('button'),label=document.createElement('span');
    wrap.className='select-picker picker-'+id;button.type='button';button.className='select-trigger';button.id=id+'-trigger';label.id=id+'-value';
    button.setAttribute('aria-label',select.getAttribute('aria-label'));button.setAttribute('aria-describedby',label.id);button.setAttribute('aria-haspopup','listbox');button.setAttribute('aria-controls','select-menu');button.setAttribute('aria-expanded','false');
    button.append(label);button.insertAdjacentHTML('beforeend','<svg viewBox="0 0 12 12" aria-hidden="true"><path d="m2 4 4 4 4-4"/></svg>');
    select.before(wrap);wrap.append(button,select);select.hidden=true;selectPickers.set(id,{select,button,label});
    button.addEventListener('click',()=>openSelectMenu(id));button.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp'].includes(event.key)){event.preventDefault();openSelectMenu(id);}});
    select.addEventListener('change',syncSelectPickers);
  }
  syncSelectPickers();
}
const texts={queued:'等待中',running:'运行中',cancelling:'正在停止',completed:'已完成',failed:'未完成',cancelled:'已停止',interrupted:'运行已中断'};
const isBusy = status => ['queued','running','cancelling'].includes(status);
const codeCopies = new Map();
const escape = value => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeLabel = date => date ? new Date(date).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',timeZone:'Asia/Shanghai'}) : '';
const duration = seconds => seconds>=60 ? `${Math.floor(seconds/60)}分${Math.floor(seconds%60)}秒` : `${Math.floor(seconds)}秒`;
function accountCheckedLabel(value){const date=new Date(value);return value&&Number.isFinite(date.getTime())?date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}):'';}
function positionAccountPanel(){
  if(!accountExpanded)return;
  const panel=$('account-panel'),main=document.querySelector('main').getBoundingClientRect(),header=document.querySelector('.topbar').getBoundingClientRect();
  const left=Math.max(0,main.left),right=Math.min(window.innerWidth,main.right),space=Math.max(0,right-left),gutter=space<=740?12:24;
  const width=Math.min(640,Math.max(0,space-gutter*2)),headerTop=Math.max(8,header.bottom+8);
  panel.style.width=width+'px';panel.style.left=(left+(space-width)/2)+'px';
  let top=headerTop,maxHeight=Math.max(0,window.innerHeight-top-12);
  if(accountAnchor==='composer'){
    const anchor=$('quota-ring').getBoundingClientRect(),above=anchor.top-headerTop-10;
    if(above>=180){maxHeight=above;panel.style.maxHeight=maxHeight+'px';top=Math.max(headerTop,anchor.top-Math.min(panel.scrollHeight,maxHeight)-8);}
  }
  panel.style.top=top+'px';panel.style.maxHeight=maxHeight+'px';
}
function renderQuota(account,checking){
  const labels={'gemini-5h':'Gemini · 5 小时','gemini-weekly':'Gemini · 每周','gemini-argon-wtu-weekly':'Gemini Argon · 每周','3p-5h':'第三方模型 · 5 小时','3p-weekly':'第三方模型 · 每周'};
  const order=Object.keys(labels),known=account.state==='known'||checking;
  const buckets=known&&account.quota&&typeof account.quota==='object'&&!Array.isArray(account.quota)?Object.entries(account.quota).slice(0,32).filter(([id,value])=>/^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$/.test(id)&&value&&typeof value==='object'):[];
  buckets.sort(([a],[b])=>(order.indexOf(a)<0?99:order.indexOf(a))-(order.indexOf(b)<0?99:order.indexOf(b))||a.localeCompare(b));
  const rows=buckets.map(([id,value])=>{
    const fraction=typeof value.remaining_fraction==='number'&&Number.isFinite(value.remaining_fraction)&&value.remaining_fraction>=0&&value.remaining_fraction<=1?value.remaining_fraction:null;
    const amount=typeof value.remaining_amount==='number'&&Number.isFinite(value.remaining_amount)&&value.remaining_amount>=0?value.remaining_amount:null;
    const percent=fraction===null?null:Math.round(fraction*1000)/10,disabled=value.disabled===true;
    const remaining=disabled?'未启用':percent!==null?`剩余 ${percent}%`:amount!==null?`余量 ${amount.toLocaleString('zh-CN',{maximumFractionDigits:2})}`:'余量未提供';
    let reset=value.reset_time?new Date(value.reset_time):null;
    if((!reset||!Number.isFinite(reset.getTime()))&&typeof value.reset_in_seconds==='number'&&Number.isFinite(value.reset_in_seconds)&&value.reset_in_seconds>=0&&account.checked_at)reset=new Date(new Date(account.checked_at).getTime()+value.reset_in_seconds*1000);
    const validReset=reset&&Number.isFinite(reset.getTime()),resetText=validReset?'重置 '+reset.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Shanghai'}):'重置时间未提供';
    return `<div class="quota-row${disabled?' quota-disabled':''}"><div class="quota-line"><span data-tooltip="${escape(id)}">${escape(labels[id]||id)}</span><strong class="${percent!==null&&percent<=20?'quota-low':''}">${escape(remaining)}</strong></div>${percent!==null&&!disabled?`<progress max="100" value="${percent}" aria-label="${escape(labels[id]||id)}剩余 ${percent}%"></progress>`:''}<small${validReset?` data-tooltip="${escape(accountCheckedLabel(reset.toISOString()))}（北京时间）"`:''}>${escape(resetText)}</small></div>`;
  }).join('');
  if($('quota-items').innerHTML!==rows)$('quota-items').innerHTML=rows;
  $('quota-status').textContent=rows?(!online?'离线 · 上次记录':checking?'刷新中 · 上次记录':'最近一次读取'):'';
  let empty='CLI 暂未返回额度，稍后可点“刷新”。';
  if(!snapshot.service?.features?.quota)empty='额度功能已更新，请关闭并重新打开控制台终端。';
  else if(!online)empty='服务已断开，重新打开控制台后刷新。';
  else if(checking)empty='正在读取额度…';
  else if(account.state==='api_key')empty='API key 模式未提供订阅额度。';
  else if(account.state!=='known')empty='确认 CLI 登录账号后可读取额度。';
  $('quota-empty').textContent=empty;$('quota-empty').hidden=!!rows;
}
function renderAccount(){
  const showAccount=accountExpanded;
  $('account-panel').hidden=!showAccount;
  $('account-toggle').hidden=false;
  $('account-toggle').textContent=showAccount?'CLI 账号 ⌃':'CLI 账号 ⌄';
  $('account-toggle').dataset.tooltip=showAccount?'隐藏账号与额度':'查看 CLI 账号与额度';
  $('account-toggle').setAttribute('aria-label',showAccount?'隐藏账号与额度':'查看 CLI 账号与额度');
  $('account-toggle').setAttribute('aria-expanded',String(showAccount));
  if(!showAccount)return;
  const account=snapshot.health.account||{},identity=account.email||account.name||'',checking=account.state==='checking'||accountRefreshPending,stamp=accountCheckedLabel(account.checked_at),age=stamp?Date.now()-new Date(account.checked_at).getTime():Infinity;
  const known=account.state==='known'&&!!identity,apiKey=account.state==='api_key',stale=(known||apiKey)&&age>300000;
  let label='身份尚未核实',state='未核实',tone='unknown';
  if(!online){label=identity||(apiKey?'API key 模式':'身份尚未核实');state='服务断开 · 上次记录';tone='offline';}
  else if(checking){label=identity||'正在读取 CLI 身份…';state='正在核对';tone='checking';}
  else if(account.state==='signed_out'){label='CLI 未登录';state='未登录';tone='signed-out';}
  else if(apiKey){label='API key 模式';state=stale?'上次核对 · 建议刷新':'无 Google 账号邮箱';tone=stale?'stale':'api-key';}
  else if(known){label=identity;state=stale?'上次核对 · 建议刷新':'已核对';tone=stale?'stale':'known';}
  $('account-panel').dataset.state=tone;
  $('account-identity').textContent=label;
  $('account-identity').dataset.tooltip=account.email&&account.name?`${account.name} · ${account.email}`:label;
  $('account-state').textContent=state;
  $('account-plan').textContent=account.plan_tier||'';$('account-plan').hidden=!account.plan_tier;
  $('account-source').textContent='来源：'+(account.source||'CLI 身份检查');
  $('account-checked').textContent=stamp?'更新 '+stamp:'尚未核对';
  $('account-checked').dateTime=stamp?account.checked_at:'';
  $('account-refresh').disabled=!online||checking;
  if($('account-switch'))$('account-switch').disabled=!online||checking||!!snapshot.service?.active_tasks||!snapshot.service?.features?.account_switch;
  if($('account-switch'))$('account-switch').dataset.tooltip=snapshot.service?.active_tasks?'请等待所有任务结束后切换账号':'打开 CC Switch，切换后刷新账号';
  $('account-refresh').textContent=checking?'读取中…':'刷新 ↻';
  const detail=account.detail||(!known&&!apiKey&&!checking&&online?'账号尚未确认，可点击“刷新”检查。':'');
  $('account-detail').textContent=detail;$('account-detail').hidden=!detail||(known&&!checking&&online);
  renderQuota(account,checking);
  positionAccountPanel();
}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3200);}
async function api(path,body){const isState=path.split('?')[0]==='/api/state'&&body===undefined,options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Console-Token':token},body:JSON.stringify(body)};if(isState&&stateETag)options.headers={'If-None-Match':stateETag};const res=await fetch(path,options);if(isState&&res.status===304)return null;const data=await res.json();if(!res.ok)throw Object.assign(new Error(data.error||'请求失败'),{status:res.status});if(isState)stateETag=res.headers.get('ETag');return data;}
function localImageBasename(value){
  let path=String(value).trim().replace(/^<|>$/g,'');
  try{path=decodeURIComponent(path);}catch(e){}
  if(!/^(?:file:\/\/|\/|~\/|[a-z]:[\\/])/i.test(path)||! /\.(?:png|jpe?g|webp|gif|avif|bmp)(?:[?#].*)?$/i.test(path))return null;
  return path.split(/[?#]/)[0].split(/[\\/]/).at(-1)||'生成图片';
}
function imageReferenceText(text){
  return String(text).replace(/`([^`]+)`/g,(whole,path)=>{const name=localImageBasename(path);return name?'`'+name+'`':whole;}).replace(/\[([^\]]*)\]\(([^)\n]+)\)/g,(whole,label,target)=>{const name=localImageBasename(target);return name?(localImageBasename(label)||label||name):whole;});
}
function inline(text){return escape(imageReferenceText(text)).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');}
function turnImages(turn){
  if(!/^[a-f0-9]{32}$/.test(turn.id)||!Array.isArray(turn.images))return [];
  return turn.images.filter(item=>item&&/^[a-f0-9]{16,64}$/.test(item.id)&&item.url===`/api/turns/${turn.id}/images/${item.id}`&&item.download_url===`${item.url}?download=1`&&/^image\/(?:png|jpeg|webp|gif|avif|bmp)$/.test(item.mime));
}
const imageName=image=>String(image.name||'生成图片').split(/[\\/]/).at(-1)||'生成图片';
const fileSize=bytes=>Number.isFinite(Number(bytes))&&Number(bytes)>0?(Number(bytes)>=1048576?`${(Number(bytes)/1048576).toFixed(1)} MB`:`${Math.max(1,Math.round(Number(bytes)/1024))} KB`):'';
function imageCards(turn){
  const images=turnImages(turn);if(!images.length)return '';
  return `<div class="image-attachments" aria-label="生成的图片">${images.map(item=>`<figure class="image-card"><button type="button" class="image-open" data-image="${item.id}" data-image-turn="${turn.id}" aria-label="预览图片：${escape(imageName(item))}"><img src="${item.url}" alt="${escape(imageName(item))}" loading="lazy" decoding="async"><span class="image-open-hint">查看大图 ↗</span></button><figcaption><span class="image-file"><strong>${escape(imageName(item))}</strong><small>${escape([item.width&&item.height?`${item.width} × ${item.height}`:'',fileSize(item.bytes)].filter(Boolean).join(' · '))}</small></span><a href="${item.download_url}" download="${escape(imageName(item))}">下载原图 ↓</a></figcaption></figure>`).join('')}</div>`;
}
function closeImagePreview(){if($('image-preview').open)$('image-preview').close();}
function updateTaskMode(){$('task-mode').textContent=$('task-kind').value==='image'?'图片生成':'计划模式';syncSelectPickers();}
function showImagePreview(turnId,imageId){
  const turn=active()?.turns.find(t=>t.id===turnId),item=turn&&turnImages(turn).find(i=>i.id===imageId);if(!item)return;
  $('preview-title').textContent=imageName(item);$('preview-image').alt=imageName(item);$('preview-image').src=item.url;
  $('preview-download').href=item.download_url;$('preview-download').download=imageName(item);$('image-preview').showModal();
}
function markdown(source,key){
  let output='',lines=String(source||'').split('\n'),list=null;
  const closeList=()=>{if(list){output+=`</${list}>`;list=null;}};
  for(let i=0;i<lines.length;i++){
    let line=lines[i];
    if(/^\s*```/.test(line)){closeList();let language=line.replace(/^\s*```/,'').trim(),code=[];while(++i<lines.length&&!/^\s*```/.test(lines[i]))code.push(lines[i]);let cid=`${key}-${i}`;codeCopies.set(cid,code.join('\n'));output+=`<div class="code-block"><div class="code-header"><span>${escape(language||'代码')}</span><button type="button" data-code="${cid}">复制代码</button></div><pre><code>${escape(code.join('\n'))}</code></pre></div>`;continue;}
    line=line.replace(/!\[([^\]]*)\]\([^)]+\)/g,(_,alt)=>alt?`[图片：${alt}]`:'[图片]');
    if(!line.trim()){closeList();continue;}
    if(/^\s*([-*_])\1{2,}\s*$/.test(line)){closeList();output+='<hr>';continue;}
    const heading=line.match(/^(#{1,6})\s+(.*)/);if(heading){closeList();let level=Math.min(heading[1].length+1,4);output+=`<h${level}>${inline(heading[2])}</h${level}>`;continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){closeList();const cells=s=>s.trim().replace(/^\||\|$/g,'').split('|');output+='<table><thead><tr>'+cells(line).map(c=>`<th>${inline(c.trim())}</th>`).join('')+'</tr></thead><tbody>';i++;while(i+1<lines.length&&lines[i+1].includes('|')){i++;output+='<tr>'+cells(lines[i]).map(c=>`<td>${inline(c.trim())}</td>`).join('')+'</tr>';}output+='</tbody></table>';continue;}
    const item=line.match(/^\s*(?:([-*])|(\d+)[.)])\s+(.*)/);if(item){let kind=item[2]?'ol':'ul';if(list!==kind){closeList();output+=`<${kind}>`;list=kind;}output+=`<li>${inline(item[3])}</li>`;continue;}
    closeList();output+=`<p>${inline(line)}</p>`;
  }closeList();return output;
}
function conversationMeta(id){return snapshot.conversations.find(c=>c.id===id);}
function turnCount(conv){return conv?.turn_count??conv?.turns.length??0;}
function sessionGroups(){return typeof snapshot!=='undefined'&&snapshot.service?.features?.session_groups===true;}
function selectedBranch(conv){if(!sessionGroups())return null;const branches=conv?.branches||{};return conv?.id===activeId&&branches[activeBranch]?activeBranch:branches[conv?.primary_branch]?conv.primary_branch:Object.keys(branches)[0]||null;}
function active(){const meta=conversationMeta(activeId),page=conversationPages.get(pageCacheKey(activeId))?.value;if(!meta)return null;if(page)return {...page,...meta,turns:page.turns,roundtable_messages:page.roundtable_messages,roundtable_meetings:page.roundtable_meetings,meeting_summaries:page.meeting_summaries||[],meeting_count:page.meeting_count??meta.meeting_count,page:page.page,summary:page.summary,turn_count:sessionGroups()?page.page.total:Math.max(turnCount(meta),turnCount(page))};return !meta.summary&&meta.turns.length>PAGE_SIZE?localConversationPage(meta,{}):meta;}
function restoredTaskKind(conv){const kind=sessionGroups()?conv?.branches?.[selectedBranch(conv)]?.last_kind:conv?.turns.at(-1)?.kind;return kind==='image'?'image':'text';}
function composerTurn(conv){const meta=conversationMeta(conv?.id)||conv,branch=selectedBranch(meta);return meta?.turns.find(t=>isBusy(t.status)&&(!branch||t.branch===branch));}
function fixedOwnerConversation(){const owner=sessionStorage.getItem('agy-codex-owner');return owner?snapshot.conversations.find(c=>c.codex_thread_id===owner):null;}
function composerConversation(){return viewMode==='panel'?active():fixedOwnerConversation();}
function composerKey(){const conv=composerConversation();return conv?pageCacheKey(conv.id):'new:'+(requestedCodexThread||sessionStorage.getItem('agy-codex-owner')||'home');}
function saveDraftRecord(key,text){
  if(text)sessionDrafts.set(key,text);else sessionDrafts.delete(key);while(sessionDrafts.size>32)sessionDrafts.delete(sessionDrafts.keys().next().value);
  try{if(!draftStore||!draftStore.write(key,text))throw new Error();draftPersisted=true;}catch(e){draftPersisted=false;if(!draftStorageWarned){draftStorageWarned=true;toast('浏览器未能保存草稿，请先保留输入内容。');}}
}
function flushDraft(){clearTimeout(draftSaveTimer);draftSaveTimer=null;if(draftPending){const {key,text}=draftPending;draftPending=null;saveDraftRecord(key,text);}}
function queueDraftSave(){draftPending={key:composerKey(),text:$('prompt').value};clearTimeout(draftSaveTimer);draftSaveTimer=setTimeout(flushDraft,300);}
function stashComposerDraft(){flushDraft();const key=composerKey();saveDraftRecord(key,$('prompt').value);return key;}
function restoreComposerDraft(previous){const key=composerKey();if(key!==previous){let text=sessionDrafts.get(key);if(text===undefined){try{text=draftStore?.read(key)||'';}catch(e){text='';}if(text)sessionDrafts.set(key,text);}$('prompt').value=text||'';$('prompt').style.height='auto';}}
function restoreFirstDraft(){if(draftRestored||(requestedCodexThread&&!boundViewApplied))return;draftRestored=true;if(!$('prompt').value)restoreComposerDraft(null);}
function clearSubmittedDraft(key,text){
  if(draftPending?.key===key&&draftPending.text===text){clearTimeout(draftSaveTimer);draftPending=null;}
  if(sessionDrafts.get(key)===text)sessionDrafts.delete(key);
  try{draftStore?.clearSent(key,text);}catch(e){toast('任务已提交，但浏览器未能清除旧草稿。');}
  if(composerKey()===key&&$('prompt').value===text){$('prompt').value='';$('prompt').style.height='38px';}
}
function rememberFixedOwner(conv){if(conv?.codex_thread_id&&!sessionStorage.getItem('agy-codex-owner'))sessionStorage.setItem('agy-codex-owner',conv.codex_thread_id);}
function boundActivityTarget(){
  if(typeof focusedMeetingID==='function'&&focusedMeetingID(typeof active==='function'?active():null))return null;
  const conv=fixedOwnerConversation();if(!conv){boundActivityPending=null;return null;}
  const owner=conv.codex_thread_id,latest=conv.turns.at(-1)?.id||null;
  if(boundActivityCursor?.owner!==owner){
    let saved=null;try{saved=JSON.parse(sessionStorage.getItem('agy-bound-activity-v1')||'null');}catch(e){}
    const valid=saved?.owner===owner&&(saved.turnId===null||typeof saved.turnId==='string');
    boundActivityCursor={owner,ready:valid,turnId:valid?saved.turnId:null};boundActivityPending=null;
  }
  const cursor=boundActivityCursor,cold=!cursor.ready,newTask=cursor.ready?!!latest&&latest!==cursor.turnId:conv.turns.some(t=>isBusy(t.status));
  const targetTurn=(cold?(conv.turns.find(t=>['running','cancelling'].includes(t.status))||conv.turns.find(t=>isBusy(t.status))):null)||conv.turns.at(-1);
  if(!cursor.ready||cursor.turnId!==latest){
    cursor.ready=true;cursor.turnId=latest;
    try{sessionStorage.setItem('agy-bound-activity-v1',JSON.stringify({owner,turnId:latest}));}catch(e){}
  }
  if(conv.auto_open_activity===false){boundActivityPending=null;return null;}
  if(newTask)boundActivityPending={owner,id:conv.id,branch:targetTurn?.branch||conv.primary_branch||null};
  if(!boundActivityPending)return null;
  // Already reading this chat: retain its history page and scroll position.
  if(viewMode==='panel'&&activeId===conv.id&&(!boundActivityPending.branch||selectedBranch(conv)===boundActivityPending.branch)){boundActivityPending=null;return null;}
  // Resume through the existing state poll after input or a dialog is finished.
  if(viewMode==='panel'&&conversationPages.get(pageCacheKey(activeId))?.value?.page?.has_newer)return null;
  if(document.hidden||submitting||bulkMode||openSelectId||!$('history-menu')?.hidden||$('prompt').value.trim()||['history-dialog','bulk-dialog','image-preview','session-dialog','related-dialog','collaboration-audit-dialog','task-queue-dialog','settings-dialog','panel-action-dialog','meeting-action-dialog','fulltext-dialog','backup-dialog','software-update-dialog','config-import-dialog'].some(id=>$(id)?.open))return null;
  const target=boundActivityPending.id;nextFollowBranch=boundActivityPending.branch;boundActivityPending=null;return target;
}
function followBoundActivity(){const id=boundActivityTarget();if(!id)return false;selectConversation(id,nextFollowBranch);toast('已进入绑定聊天的新任务');return true;}
function clearCodePrefix(prefix){if(prefix)for(const key of codeCopies.keys())if(key.startsWith(prefix+'-'))codeCopies.delete(key);}
function clearMessageRows(){$('messages').replaceChildren();renderedMessageRows.clear();renderedMessageView='';codeCopies.clear();}
function syncMessageRows(view,rows){
  const container=$('messages');let changed=false;
  if(renderedMessageView!==view){clearMessageRows();renderedMessageView=view;changed=true;}
  const wanted=new Set(rows.map(row=>row.key));
  for(const [key,old] of renderedMessageRows)if(!wanted.has(key)){old.element.remove();clearCodePrefix(old.codePrefix);renderedMessageRows.delete(key);changed=true;}
  let previous=null;
  for(const row of rows){
    let old=renderedMessageRows.get(row.key),element=old?.element;
    if(!old||old.signature!==row.signature){
      clearCodePrefix(old?.codePrefix);const template=document.createElement('template');template.innerHTML=row.html();
      const next=template.content.firstElementChild;if(!next)continue;
      const progressSummary=element?.querySelector?.('.task-progress > summary'),progressFocused=!!progressSummary&&progressSummary===document.activeElement,meetingFocused=element?.contains?.(document.activeElement)&&document.activeElement?.parentElement?.dataset?.meetingPart;
      next.dataset.renderKey=row.key;next.dataset.renderInstance=String(++renderInstance);
      if(element?.parentNode===container){container.insertBefore(next,element);element.remove();}
      if(progressFocused)next.querySelector('.task-progress > summary')?.focus({preventScroll:true});
      if(meetingFocused)next.querySelector('[data-meeting-part="'+meetingFocused+'"] > summary')?.focus({preventScroll:true});
      element=next;renderedMessageRows.set(row.key,{element,signature:row.signature,codePrefix:row.codePrefix});changed=true;
    }
    const anchor=previous?previous.nextElementSibling:container.firstElementChild;
    if(element!==anchor)container.insertBefore(element,anchor);
    previous=element;
  }
  return changed;
}
function pageTag(meta){return meta.summary?String(meta.revision):JSON.stringify([meta.title,meta.agy_id,meta.updated_at,meta.turns.map(t=>[t.id,t.status,t.response?.length,t.error,t.images?.length,t.source]),meta.roundtable_messages||[],meta.roundtable_meetings||[]]);}
function roundtableSourceURL(message){
  if(!['muse','claude'].includes(message?.provider)||typeof message.source_url!=='string'||message.source_url!==message.source_url.trim())return '';
  try{
    const url=new URL(message.source_url),hosts=message.provider==='muse'?['muse.ai','www.muse.ai']:['claude.ai','www.claude.ai'],prefix=message.provider==='muse'?'/thread/':'/chat/';
    return url.protocol==='https:'&&hosts.includes(url.hostname)&&new RegExp('^https://(?:'+hosts.map(host=>host.replaceAll('.','\\.')).join('|')+')/','i').test(message.source_url)&&!url.username&&!url.password&&!url.port&&!url.search&&!url.hash&&new RegExp('^'+prefix+'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$','i').test(url.pathname)?url.href:'';
  }catch(e){return '';}
}
function pageCacheKey(id,params={}){return sessionGroups()?id+':'+(params.branch||selectedBranch(conversationMeta(id))||''):id;}
function localConversationPage(meta,params){
  const turns=params.branch?meta.turns.filter(t=>(t.branch||meta.primary_branch||'main')===params.branch):meta.turns,total=turns.length;let start,end,index;
  if(params.before||params.after||params.around){index=turns.findIndex(t=>t.id===(params.before||params.after||params.around));if(index<0)throw new Error('页位置已变化，请返回最新对话。');}
  if(params.around){start=Math.max(0,index-Math.floor(PAGE_SIZE/2));end=Math.min(total,start+PAGE_SIZE);}else if(params.before){end=index;start=Math.max(0,end-PAGE_SIZE);}else if(params.after){start=index+1;end=Math.min(total,start+PAGE_SIZE);}else{end=total;start=Math.max(0,end-PAGE_SIZE);}
  const chosen=turns.slice(start,end);
  return {...meta,summary:false,turn_count:total,turns:chosen,roundtable_messages:roundtableMessages({...meta,turns:chosen}),roundtable_meetings:!params.before&&!params.after&&!params.around?roundtableMeetings(meta).filter(meeting=>!params.branch||meeting.branch===params.branch).slice(-10):[],page:{start:chosen.length?start+1:0,end,total,branch:params.branch||null,has_older:start>0,has_newer:end<total,first_id:chosen[0]?.id,last_id:chosen.at(-1)?.id}};
}
function roundtableMessages(conv){
  if(!Array.isArray(conv?.roundtable_messages)||!conv.roundtable_messages.length||typeof conv.codex_thread_id!=='string'||!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(conv.codex_thread_id))return [];
  const anchors=new Map((conv.turns||[]).map(turn=>[turn.id,turn])),seen=new Set(),sources=new Set(),fields=['schema_version','codex_thread_id','mode','host','rounds'];
  const valid=value=>value&&typeof value==='object'&&Object.keys(value).length===fields.length&&value.schema_version===1&&value.codex_thread_id===conv.codex_thread_id&&value.mode==='roundtable'&&['codex','agy','muse','claude'].includes(value.host)&&value.rounds===2;
  return conv.roundtable_messages.filter(message=>{
    if(!message||typeof message!=='object'||typeof message.id!=='string'||!/^[a-f0-9]{32}$/.test(message.id)||seen.has(message.id)||typeof message.source_id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$/.test(message.source_id)||sources.has(message.source_id))return false;
    const anchor=anchors.get(message.anchor_turn_id),settings=message.roundtable;
    if(!anchor||anchor.source!=='codex'||anchor.status!=='completed'||typeof anchor.response!=='string'||!anchor.response.trim()||!anchor.agy_id||!valid(anchor.roundtable)||!valid(settings)||fields.some(key=>settings[key]!==anchor.roundtable[key]))return false;
    if(anchor.roundtable_role!==(settings.host==='agy'?'host':'participant')||!['codex','muse','claude'].includes(message.provider)||!['proposal','review','summary'].includes(message.stage)||message.provenance!=='codex_observed'||message.role!==(settings.host===message.provider?'host':'participant')||message.stage==='summary'&&message.role!=='host')return false;
    if(message.branch!==(anchor.branch||conv.primary_branch||'main')||typeof message.text!=='string'||!message.text.trim()||message.text.length>12000||message.text.includes('\0')||typeof message.created_at!=='string'||!Number.isFinite(Date.parse(message.created_at)))return false;
    seen.add(message.id);sources.add(message.source_id);return true;
  });
}
function roundtableMeetings(conv){
  if(!Array.isArray(conv?.roundtable_meetings)||typeof conv.codex_thread_id!=='string'||!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(conv.codex_thread_id))return [];
  const seen=new Set(),providers=['codex','agy','muse','claude'],statuses=['pending','sending','waiting','queued','running','completed','failed','unavailable','cancelled','interrupted','cancelling'];
  return conv.roundtable_meetings.slice(-20).filter(meeting=>{
    if(!meeting||typeof meeting.id!=='string'||!/^[a-f0-9]{32}$/.test(meeting.id)||seen.has(meeting.id)||meeting.codex_thread_id!==conv.codex_thread_id)return false;
    const host=meeting.host||meeting.roundtable?.host,people=meeting.participants;
    if(!providers.includes(host)||!Array.isArray(people)||people.length<2||people.length>4||new Set(people).size!==people.length||!people.includes('codex')||!people.includes(host)||people.some(p=>!providers.includes(p))||meeting.rounds!==2)return false;
    if(typeof meeting.branch!=='string'||!/^[a-z][a-z0-9_-]{0,39}$/.test(meeting.branch)||conv.page?.branch&&meeting.branch!==conv.page.branch)return false;
    if(typeof meeting.topic!=='string'||!meeting.topic.trim()||meeting.topic.length>2000||!['active','completed','partial','cancelled'].includes(meeting.status)||!Number.isFinite(Date.parse(meeting.created_at))||!Number.isFinite(Date.parse(meeting.updated_at)))return false;
    if(!Array.isArray(meeting.slots)||meeting.slots.length!==people.length*2+1)return false;
    const slots=new Set();
    for(const slot of meeting.slots){
      const key=slot?.provider+':'+slot?.stage;
      if(!slot||!people.includes(slot.provider)||!['proposal','review','summary'].includes(slot.stage)||slot.stage==='summary'&&slot.provider!==host||slots.has(key)||!statuses.includes(slot.status))return false;
      if(typeof slot.text!=='string'||slot.text.length>12000||slot.text.includes('\0')||slot.status==='completed'&&!slot.text.trim()||typeof slot.detail!=='string'||slot.detail.length>2000)return false;
      if(slot.turn_id!==null&&slot.turn_id!==undefined&&(typeof slot.turn_id!=='string'||!/^[a-f0-9]{32}$/.test(slot.turn_id)))return false;
      slots.add(key);
    }
    if(people.some(provider=>!slots.has(provider+':proposal')||!slots.has(provider+':review'))||!slots.has(host+':summary'))return false;
    seen.add(meeting.id);return true;
  });
}
const meetingFocusByPage=new Map();
function focusedMeetingID(conv){if(!conv?.id)return null;const id=meetingFocusByPage.get(pageCacheKey(conv.id));return id&&roundtableMeetings(conv).some(item=>item.id===id)?id:null;}
function setMeetingFocus(meetingId=null){
  const conv=active(),key=pageCacheKey(conv?.id);if(!conv?.id)return;
  if(meetingId){const state=meetingPagingState(conv);if(!state.shown.some(item=>item.id===meetingId)||!state.entry)return;state.entry.meetingHistory=state.entry.meetingHistory||{meetings:state.shown,hasOlder:state.older};meetingFocusByPage.delete(key);meetingFocusByPage.set(key,meetingId);while(meetingFocusByPage.size>64)meetingFocusByPage.delete(meetingFocusByPage.keys().next().value);}
  else meetingFocusByPage.delete(key);
  const entry=conversationPages.get(key);if(entry)entry.scroll='top';render();
}
function filterFocusedMeetingRows(conv,rows){const id=focusedMeetingID(conv);if(id)rows.splice(0,rows.length,...rows.filter(row=>row.key==='meeting:'+id));}
function meetingLinkedTurnIDs(meetings){return new Set(meetings.flatMap(meeting=>meeting.slots.filter(slot=>slot.provider==='agy'&&slot.turn_id).map(slot=>slot.turn_id)));}
function pruneConversationPages(){
  for(const key of conversationPages.keys())if(!conversationMeta(key.split(':')[0])&&!conversationRequests.has(key))conversationPages.delete(key);
  for(const key of conversationPages.keys()){if(conversationPages.size<=2)break;if(key!==pageCacheKey(activeId)&&!conversationRequests.has(key))conversationPages.delete(key);}
}
function loadConversationPage(id,params={},manual=false,intent=null){
  if(!id||!conversationMeta(id))return Promise.resolve(null);
  const branch=selectedBranch(conversationMeta(id));if(sessionGroups()&&!params.branch&&branch)params={...params,branch};
  const key=pageCacheKey(id,params);
  if(intent===null){intent=(conversationIntents.get(key)||0)+(manual?1:0);if(manual)conversationIntents.set(key,intent);}
  if(intent!==(conversationIntents.get(key)||0))return Promise.resolve(null);
  const pending=conversationRequests.get(key);
  if(pending)return manual?pending.then(()=>loadConversationPage(id,params,true,intent)):pending;
  const meta=conversationMeta(id),tag=pageTag(meta),old=conversationPages.get(key);
  if(!manual&&!old?.error&&old?.tag===tag)return Promise.resolve(old);
  if(!manual&&old?.error&&Date.now()<old.retryAt)return Promise.resolve(old);
  const entry={...old,params:{...params},error:'',loading:true};conversationPages.delete(key);conversationPages.set(key,entry);
  const task=Promise.resolve().then(async()=>{
    try{
      let value;
      if(meta.summary){const {matchRole,...serverParams}=params;const query=new URLSearchParams({limit:String(PAGE_SIZE),...serverParams});value=await api('/api/conversations/'+id+'?'+query);}
      else value=localConversationPage(meta,params);
      if(intent!==(conversationIntents.get(key)||0))return null;
      if(manual){delete entry.meetingHistory;delete entry.meetingRequest;entry.meetingError='';}
      else if(entry.meetingHistory&&!params.before&&!params.after&&!params.around){
        const history=entry.meetingHistory,fresh=new Map((value.roundtable_meetings||[]).map(item=>[item.id,item])),missing=history.meetings.filter(item=>!fresh.has(item.id)).map(item=>item.id),branch=params.branch||value.page?.branch||value.primary_branch||'main';
        if(missing.length){
          try{
            const batch=await api('/api/conversations/'+id+'/meetings?'+new URLSearchParams({branch,ids:missing.join(',')}));
            if(intent!==(conversationIntents.get(key)||0)||viewMode!=='panel'||pageCacheKey(activeId)!==key)return null;
            const records=Array.isArray(batch?.meetings)?batch.meetings:[],valid=roundtableMeetings({codex_thread_id:meta.codex_thread_id,page:{branch},roundtable_meetings:records});
            if(!Array.isArray(batch?.meetings)||records.length!==missing.length||valid.length!==records.length||records.some(item=>!missing.includes(item.id)))throw new Error('圆桌历史更新返回不匹配');
            for(const item of records)fresh.set(item.id,item);entry.meetingError='';
          }catch(error){if(intent!==(conversationIntents.get(key)||0))return null;entry.meetingError='圆桌历史暂未更新，可返回最近圆桌。';}
        }
        history.meetings=history.meetings.map(item=>fresh.get(item.id)||item);value.roundtable_meetings=history.meetings;
      }
      entry.value=value;entry.tag=tag;if(manual)entry.scroll=params.around?'turn:'+params.around+':'+(params.matchRole==='user'?'user':'assistant'):params.before||params.after?'top':'bottom';
    }catch(error){if(intent===(conversationIntents.get(key)||0)){entry.error=error.message||'暂时无法载入这页对话。';entry.retryAt=Date.now()+5000;}}
    finally{entry.loading=false;conversationRequests.delete(key);pruneConversationPages();if(viewMode==='panel'&&pageCacheKey(activeId)===key&&intent===(conversationIntents.get(key)||0))render();}
    return entry;
  });
  conversationRequests.set(key,task);pruneConversationPages();if(viewMode==='panel'&&pageCacheKey(activeId)===key)render();return task;
}
function synchronizeDetail(){pruneConversationPages();if(viewMode==='panel'&&activeId)loadConversationPage(activeId,conversationPages.get(pageCacheKey(activeId))?.params||{});}
function renderPaging(conv){
  const page=conv?.page,entry=conversationPages.get(pageCacheKey(conv?.id)),total=sessionGroups()?(page?.total||0):Math.max(turnCount(conversationMeta(conv?.id)||conv),turnCount(conv),page?.total||0),loading=!!entry?.loading;
  $('turns-paging').hidden=!page||total<=PAGE_SIZE;
  $('turns-older').disabled=loading||!page?.has_older;$('turns-newer').disabled=loading||!page||page.end>=total;
  $('turns-latest').disabled=loading||!page||page.end>=total;
  $('turns-range').textContent=page?`${page.start}–${page.end} / ${total} 轮`:'';
  $('detail-error').hidden=!entry?.error;$('detail-error-text').textContent=entry?.error||'';
  $('detail-retry').disabled=loading;
}
function pageConversation(direction){
  const conv=active(),page=conv?.page;if(!conv||!page)return;closeImagePreview();
  const params={...(page.branch?{branch:page.branch}:{}),...(direction==='older'?{before:page.first_id}:direction==='newer'?{after:page.last_id}:{})};
  loadConversationPage(conv.id,params,true);
}
function meetingPagingState(conv){
  const entry=conversationPages.get(pageCacheKey(conv?.id)),shown=roundtableMeetings(conv),history=entry?.meetingHistory,total=conv?.meeting_count??conv?.meeting_summaries?.length??shown.length;
  const eligible=viewMode==='panel'&&!!conv?.codex_thread_id&&!conv.summary&&!entry?.params?.before&&!entry?.params?.after&&!entry?.params?.around&&shown.length>0;
  return {entry,shown,total,history,eligible,older:eligible&&(history?history.hasOlder:total>shown.length)};
}
function renderMeetingPaging(conv){
  const state=meetingPagingState(conv),bar=$('meetings-paging');if(!bar)return;
  const focus=focusedMeetingID(conv);$('meeting-focus-banner').hidden=!focus;$('meeting-focus-title').textContent=focus?'只看本场 · '+state.shown.find(item=>item.id===focus).topic:'';
  bar.hidden=!!focus||!state.eligible||!state.older&&!state.history&&!state.entry?.meetingError;
  $('meetings-older').hidden=!state.older;$('meetings-older').disabled=!online||!!state.entry?.meetingRequest||!!state.entry?.loading;$('meetings-older').textContent=state.entry?.meetingRequest?'载入中…':'较早圆桌';
  $('meetings-latest').hidden=!state.history;$('meetings-latest').disabled=!online||!!state.entry?.meetingRequest||!!state.entry?.loading;
  $('meetings-range').textContent=state.entry?.meetingError||'显示 '+state.shown.length+'/'+state.total+' 场'+(state.history?' · 历史':'');
}
async function loadOlderMeetings(){
  const conv=active(),state=meetingPagingState(conv);if(!online||!state.older||state.entry?.meetingRequest||state.entry?.loading)return;
  const cid=conv.id,thread=conv.codex_thread_id,branch=conv.page?.branch||selectedBranch(conversationMeta(cid))||conv.primary_branch||'main',key=pageCacheKey(cid),before=state.shown[0]?.id;
  if(!before||!state.entry)return;
  const token={},intent=conversationIntents.get(key)||0;state.entry.meetingRequest=token;state.entry.meetingError='';renderMeetingPaging(conv);
  const current=()=>{const entry=conversationPages.get(key);return entry?.meetingRequest===token&&(conversationIntents.get(key)||0)===intent&&entry.value?.codex_thread_id===thread?entry:null;};
  try{
    const result=await api('/api/conversations/'+cid+'/meetings?'+new URLSearchParams({branch,before,limit:'10'}));
    const entry=current();if(!entry||viewMode!=='panel'||activeId!==cid||pageCacheKey(activeId)!==key)return;
    const values=Array.isArray(result?.meetings)?result.meetings:[],validated=roundtableMeetings({codex_thread_id:thread,page:{branch},roundtable_meetings:values});
    if(!Array.isArray(result?.meetings)||validated.length!==values.length||values.length>10||typeof result.has_older!=='boolean'||!Number.isInteger(result.total)||result.total<state.shown.length||values.length&&result.first_id!==values[0].id||result.has_older&&!values.length)throw new Error('圆桌分页返回不匹配，原记录未替换。');
    const shown=roundtableMeetings(entry.value),ids=new Set(shown.map(item=>item.id));if(values.some(item=>ids.has(item.id)))throw new Error('圆桌分页位置已变化，请回到最近圆桌。');
    const combined=[...values,...shown].slice(0,20);entry.meetingHistory={meetings:combined,hasOlder:result.has_older};entry.value={...entry.value,roundtable_meetings:combined,meeting_count:result.total};entry.meetingError='';entry.scroll='top';
  }catch(error){const entry=current();if(entry)entry.meetingError=error.message||'较早圆桌暂时无法载入，可再次点击重试。';}
  finally{const entry=conversationPages.get(key);if(entry?.meetingRequest===token)delete entry.meetingRequest;if(viewMode==='panel'&&activeId===cid&&pageCacheKey(activeId)===key)render();}
}
function latestMeetings(){
  const conv=active();if(conv)meetingFocusByPage.delete(pageCacheKey(conv.id));const entry=conversationPages.get(pageCacheKey(conv?.id));if(!conv||!entry||entry.meetingRequest||entry.loading)return;
  delete entry.meetingHistory;entry.meetingError='';return loadConversationPage(conv.id,{},true);
}
function resourceMemoryValue(data){
  if(data?.warning)return null;
  if(typeof data?.rss_total_mb==='number'&&Number.isFinite(data.rss_total_mb)&&data.rss_total_mb>=0)return data.rss_total_mb;
  const rows=data?.processes||[];if(data?.warning||!rows.length)return null;
  if(rows.some(p=>typeof p.rss_mb!=='number'||!Number.isFinite(p.rss_mb)||p.rss_mb<0))return null;
  return rows.reduce((sum,p)=>sum+p.rss_mb,0);
}
function rememberMemorySample(data){
  const total=resourceMemoryValue(data);
  if(total===null){liveMemoryError=data?.warning||'暂未取得有效内存读数。';return;}
  liveMemoryLastGood={total,checked_at:data.checked_at,partial:!!data.omitted};liveMemoryError='';
}
function paintMemorySummary(){
  const node=$('live-memory');if(!node)return;
  const saved=liveMemoryLastGood,stale=!!liveMemoryError||!online;
  node.textContent=saved?'内存 '+(saved.partial?'≥ ':'')+(saved.total>=1000?(saved.total/1000).toFixed(2)+' GB':saved.total.toFixed(1)+' MB')+(stale?' · 待更新':''):'内存 —';
  node.classList.toggle('is-stale',stale);node.dataset.tooltip='本控制台及其启动的 CLI · RSS 合计\n不含浏览器；共享内存可能重复计入。'+(saved?.partial?'\n进程较多，当前仅汇总已列进程。':'')+(saved?.checked_at?'\n更新于 '+accountCheckedLabel(saved.checked_at):'')+(stale?'\n读取暂不可用，保留上次有效值。':'\n运行时约 5 秒、待命约 20 秒刷新；隐藏时暂停。点击查看明细。');
}
async function readMemorySummary(){
  if(liveMemoryPending||document.hidden)return;
  if(!snapshot.service)return;
  if(!snapshot.service.features?.resource_summary){liveMemoryError='实时内存需加载新版后台。';paintMemorySummary();return;}
  liveMemoryPending=true;
  try{rememberMemorySample(await api('/api/resources?summary=1'));}
  catch(error){liveMemoryError=error.message||'暂时无法读取内存。';}
  finally{liveMemoryPending=false;paintMemorySummary();if(quickTooltipTarget===$('live-memory'))paintQuickTooltip();}
}
function queueStatusTooltip(){
  const queue=snapshot.queue;if(!online)return '控制台未连接，任务状态尚未确认。';if(!queue)return '任务详情正在同步；点击查看队列。';
  const short=value=>{const text=String(value||'未命名窗口');return text.length>40?text.slice(0,40)+'…':text;};
  const section=(label,rows)=>label+' · '+rows.length+(rows.length?'\n'+rows.slice(0,4).map(t=>'• '+short(t.title)+(t.session_title&&!['当前会话','原会话'].includes(t.session_title)?' · '+short(t.session_title):'')).join('\n')+(rows.length>4?'\n另有 '+(rows.length-4)+' 项，点击查看全部':''):'\n暂无任务');
  return section('正在执行',queue.running||[])+'\n\n'+section('排队等待',queue.queued||[])+'\n\n点击打开队列';
}
function positionResources(){if(!resourceExpanded)return;const panel=$('resource-panel'),main=document.querySelector('main').getBoundingClientRect(),width=Math.min(420,Math.max(0,main.width-24));panel.style.width=width+'px';panel.style.left=(main.left+(main.width-width)/2)+'px';panel.style.bottom='18px';}
function paintResources(){
  paintMemorySummary();
  $('resource-panel').hidden=!resourceExpanded;$('live-memory').setAttribute('aria-expanded',String(resourceExpanded));
  $('resource-refresh').disabled=resourcePending;$('resource-refresh').textContent=resourcePending?'读取中…':'刷新 ↻';
  const number=value=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(1):null;
  let body='';
  if(resourceError)body='<p class="resource-error" role="status">'+escape(resourceError)+'</p>';
  else if(!resourceData)body='<p class="resource-empty">正在读取占用…</p>';
  else{
    const rows=resourceData.processes||[];
    body=rows.length?'<table><thead><tr><th>进程</th><th>内存</th><th>CPU 约</th></tr></thead><tbody>'+rows.map(p=>{const physical=number(p.footprint_mb),resident=number(p.rss_mb);return `<tr><td><strong>${escape(p.role)}</strong><small>${escape(p.name)} · ${escape(p.pid)}</small></td><td><strong>${physical??resident??'未取得'}${physical!==null||resident!==null?' MB':''}</strong><small>${physical!==null?'物理值 · RSS '+(resident??'—')+' MB':'RSS 驻留值'}</small></td><td>${number(p.cpu_percent)??'—'}%</td></tr>`;}).join('')+'</tbody></table>':'<p class="resource-empty">暂未取得进程列表。</p>';
    if(resourceData.warning)body+='<p class="resource-error">'+escape(resourceData.warning)+'</p>';
    body+='<p class="resource-note">'+escape(resourceData.memory_note||'')+'</p>';
    if(resourceData.omitted)body+='<p class="resource-note">其余 '+escape(resourceData.omitted)+' 个子进程未展开。</p>';
  }
  $('resource-body').innerHTML=body;$('resource-checked').textContent=resourceData?.checked_at?'读取时间：'+accountCheckedLabel(resourceData.checked_at):'';positionResources();renderSettingsInsights();
}
async function readResources(force=false){
  if(resourcePending)return;resourcePending=true;resourceError='';paintResources();
  try{resourceData=await api('/api/resources'+(force?'?refresh=1':''));rememberMemorySample(resourceData);}
  catch(error){resourceError=error.status===404?'占用面板需要载入新版后台。请等任务完成后重开桌面的 Antigravity.command。':error.message;}
  finally{resourcePending=false;paintResources();}
}
function closeResources(returnFocus=false){resourceExpanded=false;paintResources();if(returnFocus)$('live-memory').focus();}
function ownedSessionIds(){return new Set([...snapshot.conversations,...(snapshot.trash||[])].flatMap(c=>[c.agy_id,...Object.values(c.branches||{}).map(b=>b.agy_id),...(c.session_ids||[])]).filter(Boolean));}
function multiAIState(conv){
  const value=conv?.multi_ai_collaboration;
  const keys=(object,names)=>object&&typeof object==='object'&&!Array.isArray(object)&&Object.keys(object).length===names.length&&names.every(name=>Object.hasOwn(object,name));
  if(!conv?.codex_thread_id||!keys(value,['schema_version','codex_thread_id','enabled','providers'])||value.schema_version!==1||value.codex_thread_id!==conv.codex_thread_id||typeof value.enabled!=='boolean'||!keys(value.providers,['agy','muse','claude'])||!['agy','muse','claude'].every(p=>keys(value.providers[p],['enabled'])&&typeof value.providers[p].enabled==='boolean'))return null;
  return value;
}
function roundtableSaving(){return typeof roundtablePending!=='undefined'&&roundtablePending;}
function roundtableState(conv){
  const value=conv?.roundtable,names=['schema_version','codex_thread_id','mode','host','rounds'];
  if(!conv?.codex_thread_id||!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==names.length||!names.every(name=>Object.hasOwn(value,name))||value.schema_version!==1||value.codex_thread_id!==conv.codex_thread_id||!['assist','roundtable'].includes(value.mode)||!['codex','agy','muse','claude'].includes(value.host)||value.rounds!==2)return null;
  return value;
}
function roundtableHostLabel(host){return {codex:'Codex',agy:'Agy',muse:'Muse',claude:'Claude'}[host]||'待核实';}
function roundtableAvailability(owner,state){
  const policy=multiAIState(owner);
  if(!state||!policy)return 'invalid_state';
  if(!policy.enabled)return 'master_paused';
  if(state.mode==='assist')return 'assist_mode';
  if(state.host!=='codex'&&!policy.providers[state.host].enabled)return 'host_disabled';
  if(!Object.values(policy.providers).some(p=>p.enabled))return 'no_external_participant';
  return 'ready';
}
function roundtableOrganizationText(conv){
  if(conv?.roundtable?.mode!=='roundtable')return '';
  const policy=multiAIState(conv);if(!policy)return '圆桌许可待核实';
  if(!policy.enabled)return '新圆桌协作已暂停 · 已发任务继续';
  const host=conv.roundtable.host;if(host!=='codex'&&policy.providers[host]?.enabled!==true)return '主持已暂停 · 待组织';
  const meetings=Array.isArray(conv.meeting_summaries)?conv.meeting_summaries:typeof roundtableMeetings==='function'?roundtableMeetings(conv):[],active=meetings.filter(item=>item.codex_thread_id===conv.codex_thread_id&&item.status==='active');
  return active.length?'允许圆桌协作 · '+active.length+' 场已组织':'允许圆桌协作 · 待组织';
}
function renderRoundtableSettings(owner){
  const state=roundtableState(owner),policy=multiAIState(owner),supported=snapshot.service?.features?.roundtable_host===true,bound=!!owner?.codex_thread_id;
  const disabled=!bound||!state||!policy||!online||collaborationPending||roundtableSaving()||!supported,host=$('roundtable-host-toggle');
  host.dataset.conversation=owner?.id||'';host.dataset.thread=owner?.codex_thread_id||'';host.disabled=disabled;
  host.textContent=roundtableSaving()?'保存中…':'主持：'+roundtableHostLabel(state?.host)+' ▾';
  host.setAttribute('aria-expanded',String(typeof roundtableHostsExpanded!=='undefined'&&roundtableHostsExpanded));
  $('roundtable-host-menu').hidden=!(typeof roundtableHostsExpanded!=='undefined'&&roundtableHostsExpanded);
  const enabled=$('roundtable-enabled');enabled.dataset.conversation=owner?.id||'';enabled.dataset.thread=owner?.codex_thread_id||'';enabled.disabled=disabled;enabled.setAttribute('aria-checked',String(state?.mode==='roundtable'));enabled.textContent=roundtableSaving()?'保存中…':!supported?'待更新':!state&&bound?'待核实':state?.mode==='roundtable'?'开':'关';
  for(const name of ['codex','agy','muse','claude']){const button=document.querySelector('[data-roundtable-host="'+name+'"]');if(button){button.disabled=disabled||(name!=='codex'&&policy?.providers[name]?.enabled!==true);button.setAttribute('aria-pressed',String(state?.host===name));button.dataset.tooltip=name==='codex'?'Codex 组织讨论并独立核验':policy?.providers[name]?.enabled?'负责组织问题、归纳分歧和总结':'先开启 '+roundtableHostLabel(name)+' 协作许可';}}
  const reason=roundtableAvailability(owner,state),labels={assist_mode:'日常协作 · 主持偏好保留，按需请助手参与',master_paused:'总协作已暂停；主持与参与方选择保留',host_disabled:roundtableHostLabel(state?.host)+' 已暂停；先开启该提供方或换主持',no_external_participant:'尚未开启参与方；圆桌暂不派发',invalid_state:'圆桌设置待核实，暂不派发'};
  const people=policy?['Codex',...['agy','muse','claude'].filter(p=>policy.providers[p].enabled).map(roundtableHostLabel)]:[];
  $('roundtable-status').textContent=!bound?'进入本聊天固定页后选择主持':!supported?'主持设置需加载新版控制台':reason==='ready'?(roundtableOrganizationText(owner)||'会议计划')+' · 2 轮 · '+people.join('、'):labels[reason];
}
async function setRoundtablePreference(conv,patch){
  if(!conv?.codex_thread_id||!roundtableState(conv)||!multiAIState(conv)||collaborationPending||roundtableSaving()||!online||snapshot.service?.features?.roundtable_host!==true)return;
  if(Object.keys(patch).some(k=>!['host','mode'].includes(k))||patch.host!==undefined&&!['codex','agy','muse','claude'].includes(patch.host)||patch.mode!==undefined&&!['assist','roundtable'].includes(patch.mode))return;
  if(patch.host&&patch.host!=='codex'&&multiAIState(conv).providers[patch.host].enabled!==true)return;
  const cid=conv.id,thread=conv.codex_thread_id;let saved=false;roundtablePending=true;$('roundtable-error').hidden=true;renderSettings();
  try{
    const result=await api('/api/conversations/'+cid+'/roundtable',{codex_thread_id:thread,...patch,...(patch.host?{mode:'roundtable'}:{})});
    if(!roundtableState({codex_thread_id:thread,roundtable:result}))throw new Error('保存返回的聊天或主持设置不匹配');
    const current=conversationMeta(cid);if(current?.codex_thread_id===thread){current.roundtable=result;delete current.roundtable_plan;}
    saved=true;roundtableHostsExpanded=false;await refresh();toast('本聊天'+(result.mode==='roundtable'?'圆桌主持：'+roundtableHostLabel(result.host):'已改为日常协作')+'；设置不会立即调用');
  }catch(error){const text=(saved?'主持设置已保存，暂未同步：':'主持设置未保存：')+error.message;toast(text);if(settingsOwner()?.id===cid){$('roundtable-error').textContent=text;$('roundtable-error').hidden=false;}}
  finally{roundtablePending=false;renderSessions();renderSettings();}
}
function renderMultiAISettings(owner){
  const state=multiAIState(owner),supported=snapshot.service?.features?.multi_ai_collaboration===true,bound=!!owner?.codex_thread_id;
  const button=$('settings-collaboration');
  $('settings-chat-scope').textContent=bound?'本聊天：'+owner.title:'请先进入固定聊天';
  button.dataset.conversation=owner?.id||'';button.dataset.thread=owner?.codex_thread_id||'';
  button.disabled=!bound||!state||!online||collaborationPending||roundtableSaving()||!supported;
  button.setAttribute('aria-checked',String(state?.enabled===true));button.textContent=collaborationPending?'保存中…':!supported?'待更新':!state&&bound?'待核实':state?.enabled?'开':'关';
  $('multi-ai-expand').setAttribute('aria-expanded',String(multiAIExpanded));$('multi-ai-panel').hidden=!multiAIExpanded;
  for(const name of ['agy','muse','claude']){
    const control=$('provider-'+name);control.dataset.conversation=owner?.id||'';control.dataset.thread=owner?.codex_thread_id||'';
    control.disabled=!bound||!state||!online||collaborationPending||roundtableSaving()||!supported;control.setAttribute('aria-checked',String(state?.providers[name]?.enabled===true));control.textContent=state?.providers[name]?.enabled?'开':'关';
  }
  $('multi-ai-note').textContent=!bound?'请进入本聊天固定入口后设置。':!supported?'请先加载新版控制台。':!state?'协作状态待核实，暂不派发。':!state.enabled?'总协作已暂停；提供方选择保留，已发送任务继续。':'Skill · 活跃任务按计划组织；开启仅保存许可，不会唤醒空闲 Codex。';
}
function renderCollaborationControl(conv){
  const button=$('gpt-collaboration'),bound=!!conv?.codex_thread_id,state=multiAIState(conv),supported=snapshot.service?.features?.multi_ai_collaboration===true;
  button.hidden=!bound;button.disabled=!online||collaborationPending||roundtableSaving();button.setAttribute('aria-expanded',String($('settings-dialog').open&&multiAIExpanded));button.dataset.enabled=String(state?.enabled===true);
  $('gpt-collaboration-label').textContent=!supported?'多 AI 协作 · 待更新':!state?'多 AI 协作 · 待核实':!state.enabled?'多 AI 协作 · 暂停':'多 AI 协作';
  button.dataset.tooltip='按本聊天选择 Agy、Muse、Claude；点击查看协作总开关和提供方偏好。开关不是运行状态。';
}
async function setMultiAIPreference(conv,patch){
  if(!conv?.codex_thread_id||!multiAIState(conv)||collaborationPending||roundtableSaving()||!online||snapshot.service?.features?.multi_ai_collaboration!==true)return;
  const cid=conv.id,thread=conv.codex_thread_id;let saved=false;collaborationPending=true;renderCollaborationControl(conversationMeta(activeId));renderSettings();
  try{
    const result=await api('/api/conversations/'+cid+'/multi-ai-collaboration',{codex_thread_id:thread,...patch});
    if(!multiAIState({codex_thread_id:thread,multi_ai_collaboration:result}))throw new Error('保存返回的聊天或协作状态不匹配');
    const current=conversationMeta(cid);if(current?.codex_thread_id===thread){current.multi_ai_collaboration=result;current.gpt_collaboration=result.enabled&&result.providers.agy.enabled;}
    saved=true;await refresh();toast(patch.enabled===false?'本聊天新协作已暂停，已发送任务继续':patch.enabled===true?'本聊天协作总开关已开启':'提供方偏好已保存，不会立即调用');
  }catch(error){toast((saved?'设置已保存，暂未同步：':'协作设置未保存：')+error.message);}
  finally{collaborationPending=false;renderSessions();renderSettings();}
}
async function setCollaborationPreference(conv,enabled){return setMultiAIPreference(conv,{enabled});}
function openMultiAISettings(){multiAIExpanded=true;openSettings();renderCollaborationControl(conversationMeta(activeId));}
function startupMetadata(value){return value?.schema_version===1&&typeof value.policy_path==='string'&&typeof value.installation_root==='string'&&typeof value.launcher?.path==='string'?value:null;}
function consoleStartupSupported(){return snapshot.service?.features?.console_startup===true||snapshot.service?.features?.multi_ai_collaboration===true;}
function renderConsoleStartupSettings(owner){
  const metadata=startupMetadata(snapshot.service?.console_startup)||startupMetadata(owner?.console_startup)||consoleStartupData,path=metadata?.launcher.path||'',button=$('settings-auto-start');
  const supported=consoleStartupSupported();
  $('console-startup-summary').textContent=consoleStartupPending?'读取中…':path?path.split(/[\\/]/).pop():!supported?'需加载新版控制台':'未设置启动入口';
  $('console-startup-summary').title=path;
  $('console-startup-expand').setAttribute('aria-expanded',String(consoleStartupExpanded));$('console-startup-panel').hidden=!consoleStartupExpanded;
  button.dataset.conversation=owner?.id||'';button.dataset.thread=owner?.codex_thread_id||'';button.disabled=!online||!supported||!owner?.codex_thread_id||typeof owner.auto_start_console!=='boolean'||consoleAutoStartPending;
  button.setAttribute('aria-checked',String(owner?.auto_start_console===true));button.textContent=consoleAutoStartPending?'保存中…':owner?.codex_thread_id&&typeof owner.auto_start_console!=='boolean'?'待核实':owner?.auto_start_console===true?'开':'关';
  const input=$('console-launcher-path');if(!consoleLauncherDirty&&document.activeElement!==input)input.value=path;
  input.disabled=!online||!supported||consoleStartupSaving;$('console-launcher-save').disabled=!online||!supported||consoleStartupSaving;
  $('console-startup-note').textContent=consoleStartupSaving?'正在保存本机启动入口…':!path?'填写具体启动文件；留空保存可清除。此处不会执行入口。':'启动入口供本机共享；开关仅控制本聊天，保存不会启动。';
}
async function loadConsoleStartup(){
  if(consoleStartupPending||consoleStartupSaving||!online||!consoleStartupSupported())return;
  const seq=++consoleStartupSequence;consoleStartupPending=true;renderSettings();
  try{const value=await api('/api/console-startup');if(!startupMetadata(value))throw new Error('启动策略格式不正确');if(seq===consoleStartupSequence){consoleStartupData=value;if(snapshot.service)snapshot.service.console_startup=value;}}
  catch(error){if(seq===consoleStartupSequence){$('console-startup-error').textContent='读取启动入口失败：'+error.message;$('console-startup-error').hidden=false;}}
  finally{if(seq===consoleStartupSequence){consoleStartupPending=false;renderSettings();}}
}
async function saveConsoleLauncher(){
  if(consoleStartupSaving||!online||!consoleStartupSupported())return;
  const path=$('console-launcher-path').value.trim();++consoleStartupSequence;consoleStartupPending=false;consoleStartupSaving=true;$('console-startup-error').hidden=true;renderSettings();
  try{
    const value=await api('/api/console-startup',{launcher_path:path});if(!startupMetadata(value))throw new Error('启动入口返回格式不正确');
    consoleStartupData=value;if(snapshot.service)snapshot.service.console_startup=value;consoleLauncherDirty=false;toast(path?'启动入口已保存，不会立即执行':'已清除本机启动入口');
  }catch(error){$('console-startup-error').textContent='启动入口未保存：'+error.message;$('console-startup-error').hidden=false;}
  finally{consoleStartupSaving=false;renderSettings();}
}
async function setConsoleAutoStart(conv,enabled){
  if(!conv?.codex_thread_id||typeof conv.auto_start_console!=='boolean'||consoleAutoStartPending||!online||!consoleStartupSupported())return;
  const cid=conv.id,thread=conv.codex_thread_id;let saved=false;consoleAutoStartPending=true;renderSettings();
  try{
    const value=await api('/api/conversations/'+cid+'/console-startup',{codex_thread_id:thread,auto_start_console:enabled});
    if(value.codex_thread_id!==thread||typeof value.auto_start_console!=='boolean')throw new Error('保存返回的聊天不匹配');
    const current=conversationMeta(cid);if(current?.codex_thread_id===thread)current.auto_start_console=value.auto_start_console;
    saved=true;await refresh();toast(value.auto_start_console?'已允许本聊天活跃任务按需启动控制台，不会立即启动':'本聊天已关闭自动启动控制台');
  }catch(error){toast((saved?'自动启动设置已保存，暂未同步：':'自动启动设置未保存：')+error.message);}
  finally{consoleAutoStartPending=false;renderSettings();}
}
async function setAutoOpenPreference(conv,enabled){
  if(!conv?.codex_thread_id||autoOpenPending||!online||snapshot.service?.features?.panel_auto_open!==true)return;
  const cid=conv.id;let saved=false;autoOpenPending=true;renderSettings();
  try{
    const result=await api('/api/conversations/'+cid+'/auto-open',{enabled});
    if(result.id!==cid)throw new Error('保存返回的聊天不匹配');
    const current=conversationMeta(cid);if(current)Object.assign(current,{auto_open_activity:result.enabled,auto_open_activity_updated_at:result.updated_at});
    saved=true;
    await refresh();toast(result.enabled?'本聊天已开启自动打开活动窗口':'本聊天已关闭自动打开，协作任务继续');
  }catch(error){toast((saved?'设置已保存，暂未同步最新状态：':'自动打开设置未保存：')+error.message);}
  finally{autoOpenPending=false;renderSettings();}
}
function renderSessions(){
  const conv=conversationMeta(activeId),branches=Object.values(conv?.branches||{}),show=sessionGroups()&&viewMode==='panel'&&(branches.length>0||!!conv?.codex_thread_id);
  $('session-toolbar').hidden=!show;$('session-related-list').hidden=!show||!relatedExpanded;
  renderCollaborationControl(show?conv:null);
  $('collaboration-records').hidden=!show||!conv?.codex_thread_id;$('collaboration-records').disabled=!online;
  if(!show)return;
  const chosen=selectedBranch(conv),tabs=branches.map(b=>`<button type="button" role="tab" aria-selected="${b.id===chosen}" data-session-open="${escape(b.id)}" data-tooltip="${escape(b.title)}${b.id===conv.primary_branch?' · 默认会话':''} · ${Number(b.turn_count||0)}轮">${b.busy_count?'<span class="session-busy">●</span>':''}${escape(b.title)}</button>`).join('');
  if($('session-tabs').innerHTML!==tabs)$('session-tabs').innerHTML=tabs;
  $('session-add').disabled=!online||submitting;$('session-rename').disabled=!online||!chosen;
  const current=conv.branches?.[chosen],related=activityState.sessions.filter(s=>s.related&&s.owner_conversation_id===conv.id&&(!chosen||s.owner_branch===chosen)||current?.adopted&&s.id===current.agy_id);
  $('session-related-toggle').hidden=!related.length;$('session-related-toggle').textContent='关联 '+related.length;$('session-related-toggle').setAttribute('aria-expanded',String(relatedExpanded));
  if(!related.length){relatedExpanded=false;$('session-related-list').hidden=true;}
  if(relatedExpanded){const html=related.map(s=>`<button type="button" data-related-session="${escape(s.id)}"><strong>${escape(externalTitle(s))}</strong><span>${escape(activityLabels[s.status]||'状态待确认')} ↗</span></button>`).join('');if($('session-related-list').innerHTML!==html)$('session-related-list').innerHTML=html;}
}
function collaborationRecord(turn){
  return {id:turn.id,branch:turn.branch,model:turn.model,status:turn.status,created_at:turn.created_at,ended_at:turn.ended_at,
    title:String(turn.prompt||'').split('\n').find(s=>s.trim())?.replace(/^\/compact\s*/,'').slice(0,100)||'Codex 派发任务',
    request:String(turn.prompt||'').slice(0,500),response:String(turn.response||'').slice(0,500),images:(turn.images||[]).length,
    elapsed:turn.elapsed,usage:turn.usage||{},usage_scope:turn.usage_scope,
    review:turn.review?{summary:turn.review.summary.slice(0,600),verification:turn.review.verification,
      items:turn.review.items.map(i=>({decision:i.decision}))}:null};
}
function collaborationStateText(conv){
  const roundtableText=roundtableOrganizationText(conv);if(roundtableText)return roundtableText;
  const stage=conv?.collaboration_state?.stage;
  const policy=multiAIState(conv);
  if(policy?.enabled===false||policy?.providers.agy.enabled===false||!policy&&conv?.gpt_collaboration===false)return ['queued','working'].includes(stage)?'Agy 任务继续 · 新 Agy 协作已暂停':'新 Agy 协作已暂停';
  return {idle:'协作已开启 · 待命',queued:'Codex 已派发 · 排队中',working:'Codex 已派发 · Agy 处理中',awaiting_codex:'Agy 已回复 · 待 Codex 整合',verified:'Codex 已整合 · 验证通过',reviewed:'Codex 已整合 · 查看验证结果',failed:'Agy 未完成 · 查看协作记录'}[stage]||'协作已开启 · 查看实际派发记录';
}
function reviewLabel(review){return !review?'':review.verification?.status==='passed'?'Codex 已整合 · 验证通过':review.verification?.status==='partial'?'Codex 已整合 · 部分验证':'Codex 已整合 · 尚未验证';}
function reviewMarkup(review,detail=false){
  if(!review)return '';
  const decisions={accepted:'采纳',rejected:'驳回',deferred:'暂缓'};
  return `<section class="codex-review"><strong>${escape(reviewLabel(review))}</strong><p>${escape(review.summary)}</p>${detail?'<ul>'+review.items.map(i=>`<li><b>${decisions[i.decision]||'待确认'}</b> · ${escape(i.suggestion)}<p>${escape(i.reason)}</p></li>`).join('')+'</ul><p class="review-verification">'+escape(review.verification.details)+'</p>':'<small>'+Object.entries(decisions).map(([key,label])=>label+' '+review.items.filter(i=>i.decision===key).length).join(' · ')+'</small>'}</section>`;
}
function tokenUsageText(turn){
  const u=turn.usage||{},scope=turn.usage_scope==='turn'?'本轮':turn.usage_scope==='conversation'?'会话累计':'CLI 原始统计';
  return [scope,typeof u.input_tokens==='number'?'输入 '+u.input_tokens.toLocaleString():'',typeof u.output_tokens==='number'?'输出 '+u.output_tokens.toLocaleString():'',typeof u.cache_read_tokens==='number'?'缓存读取 '+u.cache_read_tokens.toLocaleString():'',typeof u.input_tokens!=='number'&&typeof u.output_tokens!=='number'&&typeof u.total_tokens==='number'?'总计 '+u.total_tokens.toLocaleString():''].filter(Boolean).join(' · ');
}
function renderCollaborationAudit(){
  const audit=collaborationAudit,records=audit.records;
  $('collaboration-audit-summary').textContent=audit.filtered?`已读取 ${records.length} 条实际派发记录。`:`已读取最近 ${audit.scanned} 轮，找到 ${records.length} 条 Codex 派发。`;
  $('collaboration-audit-list').innerHTML=records.map(t=>`<article class="collaboration-record"><div class="collaboration-record-meta"><span>Codex → Agy</span><strong class="status-${t.status}">${escape(texts[t.status]||t.status)}</strong><time>${escape(timeLabel(t.created_at))}</time></div><h3>${escape(t.title)}</h3><p class="collaboration-model">${escape(t.model)}${t.elapsed?' · '+escape(duration(t.elapsed)):''}</p><p class="collaboration-preview">${escape(t.response|| (t.images?'已返回 '+t.images+' 张图片':isBusy(t.status)?'等待 Agy 回复…':'尚无回复'))}</p><small>${escape(tokenUsageText(t))}</small>${reviewMarkup(t.review)}<button type="button" data-collaboration-turn="${t.id}">原始请求与回复 ↗</button></article>`).join('')||'<p class="collaboration-empty">这些轮次中还没有 Codex 派发记录。可以读取更早记录；开关开启本身不会启动任务。</p>';
  $('collaboration-audit-older').hidden=!audit.hasOlder||records.length>=60;$('collaboration-audit-older').disabled=audit.pending;
}
async function loadCollaborationAudit(older=false){
  if(collaborationAudit.pending)return;
  const cid=collaborationAudit.cid,seq=++collaborationAudit.sequence;collaborationAudit.pending=true;
  $('collaboration-audit-refresh').disabled=true;$('collaboration-audit-older').disabled=true;$('collaboration-audit-error').hidden=true;
  try{
    const page=await api('/api/conversations/'+cid+'?'+new URLSearchParams({limit:'20',source:'codex',...(older&&collaborationAudit.before?{before:collaborationAudit.before}:{})}));
    if(seq!==collaborationAudit.sequence||!$('collaboration-audit-dialog').open)return;
    if(!older){collaborationAudit.records=[];collaborationAudit.scanned=0;}
    const seen=new Set(collaborationAudit.records.map(t=>t.id));
    for(const turn of [...page.turns].reverse())if(turn.source==='codex'&&!seen.has(turn.id)){collaborationAudit.records.push(collaborationRecord(turn));seen.add(turn.id);}
    collaborationAudit.records=collaborationAudit.records.slice(0,60);collaborationAudit.scanned+=page.turns.length;
    collaborationAudit.before=page.page?.first_id;collaborationAudit.hasOlder=!!page.page?.has_older;collaborationAudit.filtered=page.page?.source==='codex';
    renderCollaborationAudit();
  }catch(e){if(seq===collaborationAudit.sequence&&$('collaboration-audit-dialog').open){$('collaboration-audit-error').textContent=e.message;$('collaboration-audit-error').hidden=false;}}
  finally{if(seq===collaborationAudit.sequence){collaborationAudit.pending=false;$('collaboration-audit-refresh').disabled=false;$('collaboration-audit-older').disabled=false;}}
}
async function openCollaborationAudit(){
  const conv=conversationMeta(activeId);if(!conv?.codex_thread_id)return;
  hideQuickTooltip();collaborationAudit={cid:conv.id,sequence:collaborationAudit.sequence+1,records:[],before:null,hasOlder:false,scanned:0,pending:false};
  $('collaboration-audit-list').textContent='正在读取实际派发记录…';$('collaboration-audit-summary').textContent='';$('collaboration-audit-detail').hidden=true;$('collaboration-audit-list').hidden=false;
  $('collaboration-audit-back').hidden=true;$('collaboration-audit-dialog').showModal();await loadCollaborationAudit();
}
async function showCollaborationTurn(id,expectedMeeting=null){
  if(collaborationAudit.pending||!collaborationAudit.records.some(t=>t.id===id))return;
  const cid=collaborationAudit.cid,seq=++collaborationAudit.sequence;
  $('collaboration-audit-detail').hidden=false;$('collaboration-audit-list').hidden=true;$('collaboration-audit-back').hidden=false;$('collaboration-audit-detail').textContent='正在读取原始内容…';
  try{
    const result=await api('/api/conversations/'+cid+'/turns/'+id);
    if(seq!==collaborationAudit.sequence||!$('collaboration-audit-dialog').open)return;
    const t=result.turn;
    if(!t||t.id!==id||expectedMeeting&&(t.meeting_id!==expectedMeeting.id||t.meeting_stage!==expectedMeeting.stage||(t.branch||'main')!==expectedMeeting.branch||t.source!=='codex'))throw new Error('原始任务与当前会议不匹配，已停止显示。');
    $('collaboration-audit-detail').innerHTML=`<p class="collaboration-model">${escape(t.model)} · ${escape(texts[t.status]||t.status)} · ${escape(tokenUsageText(t))}</p>${reviewMarkup(t.review,true)}<h3>Codex 发给 Agy</h3><pre>${escape(t.prompt)}</pre><h3>Agy 原始回复</h3><pre>${escape(t.response||'尚无文字回复')}</pre>${t.images?.length?'<p>已返回 '+t.images.length+' 张图片，可在原会话查看。</p>':''}${t.error?'<details><summary>错误原文</summary><pre>'+escape(t.error)+'</pre></details>':''}${t.completion_evidence?'<details><summary>完成状态核验依据</summary><pre>'+escape(JSON.stringify(t.completion_evidence,null,2))+'</pre></details>':''}`;
  }catch(e){if(seq===collaborationAudit.sequence)$('collaboration-audit-detail').textContent=e.message;}
}
async function openMeetingTurn(meetingId,turnId){
  const conv=active(),meeting=roundtableMeetings(conv).find(item=>item.id===meetingId),slot=meeting?.slots.find(item=>item.provider==='agy'&&item.turn_id===turnId);
  if(!conv?.id||!slot||!online)return;
  hideQuickTooltip();collaborationAudit={cid:conv.id,sequence:collaborationAudit.sequence+1,records:[{id:turnId}],before:null,hasOlder:false,scanned:1,pending:false};
  $('collaboration-audit-list').innerHTML='<p class="collaboration-empty">本场会议的官方 CLI 原始任务与完整回复。</p><button type="button" data-collaboration-turn="'+escape(turnId)+'">查看原始任务 ↗</button>';
  $('collaboration-audit-summary').textContent=meeting.topic;$('collaboration-audit-error').hidden=true;$('collaboration-audit-detail').hidden=true;$('collaboration-audit-list').hidden=false;$('collaboration-audit-back').hidden=true;
  $('collaboration-audit-dialog').showModal();await showCollaborationTurn(turnId,{id:meetingId,stage:slot.stage,branch:meeting.branch});
}
function rememberSessionResult(cid,branch){const conv=conversationMeta(cid);if(!conv)return;conv.branches=conv.branches||{};if(!conv.branches[branch.id]){conv.branches[branch.id]=branch;if(!conv.primary_branch){conv.primary_branch=branch.id;conv.agy_id=branch.agy_id||null;}}}
function openSessionDialog(mode='create',source=null){
  if(!sessionGroups())return;
  closeSelectMenu();closeHistoryMenu();const conv=conversationMeta(activeId)||fixedOwnerConversation(),branch=conv?.branches?.[selectedBranch(conv)];
  if(mode!=='associate'&&!conv)return;
  sessionDialogState={mode,cid:conv?.id,key:mode==='rename'?branch?.id:null,agy_id:source?.id};
  $('session-dialog-title').textContent=mode==='associate'?'关联已有会话':mode==='rename'?'重命名会话':'新建工作会话';
  $('session-dialog-note').textContent=mode==='associate'?'将复用所选原会话，保留它的上下文与历史。请先退出原窗口中的 CLI。':mode==='rename'?'只修改显示名称，保留原会话与上下文。':'为需要独立上下文的工作命名；创建不会调用模型。';
  $('session-name').value=mode==='rename'?branch?.title||'':mode==='associate'?(source?.title||'关联会话').slice(0,40):'';
  $('session-owner-label').hidden=mode!=='associate';$('session-owner').hidden=mode!=='associate';
  $('session-owner').innerHTML=snapshot.conversations.filter(c=>c.codex_thread_id).map(c=>`<label><input type="radio" name="session-owner" value="${c.id}" ${c.id===conv?.id?'checked':''}><span>${escape(c.title)}</span></label>`).join('');
  if(mode==='associate'&&!$('session-owner').querySelector(':checked'))$('session-owner').querySelector('input')?.click();
  $('session-dialog-confirm').textContent=mode==='associate'?'关联':mode==='rename'?'保存':'创建';$('session-dialog-confirm').disabled=false;$('session-dialog-error').hidden=true;
  $('session-dialog').showModal();$('session-name').focus();
}
async function showRelatedSession(id){
  const seq=++relatedRequest;$('related-dialog-title').textContent=externalTitle(activityState.sessions.find(s=>s.id===id)||{id,title:'关联会话'});
  $('related-messages').textContent='正在读取关联会话…';$('related-dialog').showModal();
  try{const detail=await api('/api/activity/sessions/'+id);if(seq!==relatedRequest||!$('related-dialog').open)return;
    $('related-messages').innerHTML=detail.messages.length?detail.messages.map(m=>`<article><div class="message-label">${m.role==='user'?'原会话请求':'Antigravity'} <time>${escape(timeLabel(m.created_at))}</time></div><div class="markdown">${m.role==='user'?escape(m.text):markdown(m.text,'related-'+m.id)}</div></article>`).join(''):'<p>暂无可读取的消息。</p>';
  }catch(e){if(seq===relatedRequest&&$('related-dialog').open)$('related-messages').textContent=e.message;}
}
function historySettings(source,id){return snapshot.history_overrides?.[`${source}:${id}`]||{};}
function requestTitle(text){
  text=String(text||'');const wrapped=text.match(/<USER_REQUEST>\s*([\s\S]*?)\s*<\/USER_REQUEST>/);if(wrapped)text=wrapped[1];
  text=text.replace(/^\s*\/(?:plan|agent|chat|fast)\b\s*/i,'').split(/^\s*(?:<reference_material\b|交付要求\s*[:：]|established evidence\s*:)/im)[0];
  const goal=text.match(/^\s*(?:#{1,6}\s*)?(?:任务标题|标题|主题|目标|任务|goal|objective|task|topic)\s*[:：]\s*([^\n]+)/im);
  text=(goal?goal[1]:text.split('\n').find(line=>line.trim())||'').replace(/^\s*(?:#{1,6}\s*|[-*]\s+)/,'').replace(/^用户希望\s*(?:Codex\s*(?:和|与)\s*Antigravity\s*一起\s*)?/i,'').replace(/\s+/g,' ').trim();
  return [...text].length>58?[...text].slice(0,58).join('')+'…':text;
}
function isDirectoryTitle(session){const parts=String(session.workspace||'').replace(/\/$/,'').split('/'),name=parts.at(-1),parent=parts.at(-2);return !session.title||session.title===name||session.title===parent+' / '+name||/^Ag(?:y)? CLI [a-f0-9]{6,}/i.test(session.title);}
function externalTitle(session){
  const custom=historySettings('external',session.id).title;if(custom)return custom;
  if(session.request_title)return session.request_title;
  const cached=externalTitleCache.get(session.id);if(cached?.title)return cached.title;
  return isDirectoryTitle(session)?(cached?'未命名聊天 · '+session.id.slice(0,6):'正在读取聊天…'):session.title;
}
async function hydrateActivityTitles(){
  if(titleHydrating||!activityOnline)return;
  const owned=ownedSessionIds();
  const pending=activityState.sessions.filter(s=>!owned.has(s.id)&&!historySettings('external',s.id).title&&!('request_title' in s)&&!externalTitleCache.get(s.id)?.title&&(externalTitleCache.get(s.id)?.retryAt||0)<Date.now()).slice(0,8);
  if(!pending.length)return;titleHydrating=true;
  try{for(let i=0;i<pending.length;i+=2){await Promise.all(pending.slice(i,i+2).map(async session=>{
    try{const detail=await api(`/api/activity/sessions/${session.id}`),first=detail.messages?.find(m=>m.role==='user'&&m.text?.trim());externalTitleCache.set(session.id,{title:detail.session?.request_title||requestTitle(first?.text),retryAt:Date.now()+10000});}
    catch(e){externalTitleCache.set(session.id,{title:'',retryAt:Date.now()+30000});}
  }));render();}while(externalTitleCache.size>256)externalTitleCache.delete(externalTitleCache.keys().next().value);}
  finally{titleHydrating=false;}
}
function historyEntries(){
  const ownedIds=ownedSessionIds();
  const panel=snapshot.conversations.map(c=>{const turn=c.turns.find(t=>isBusy(t.status))||c.turns.at(-1);return {key:'panel:'+c.id,source:'panel',id:c.id,title:c.title,updated_at:c.updated_at,status:turn?texts[turn.status]:'等待任务',busy:!!turn&&isBusy(turn.status),hidden:!!historySettings('panel',c.id).hidden,selected:viewMode==='panel'&&activeId===c.id};});
  const external=activityState.sessions.filter(s=>!ownedIds.has(s.id)&&!s.owner_conversation_id).map(s=>({key:'external:'+s.id,source:'external',id:s.id,title:externalTitle(s),updated_at:s.updated_at,status:activityLabels[s.status]||'状态待确认',busy:s.active&&['running','waiting'].includes(s.status),hidden:!!historySettings('external',s.id).hidden,selected:viewMode==='external'&&externalId===s.id,canAssociate:sessionGroups()&&!s.active&&s.status==='ended'&&!s.parent_id&&s.parent_known!==false}));
  const trash=(snapshot.trash||[]).map(c=>({key:'panel:'+c.id,source:'panel',id:c.id,title:c.title,updated_at:c.deleted_at||c.updated_at,status:'回收站 · 可恢复',deleted:true}));
  return [...panel,...external,...trash].map(e=>({...e,pinned:!e.deleted&&historyPins.has(e.key)})).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||(Date.parse(b.updated_at)||0)-(Date.parse(a.updated_at)||0));
}
function renderHistory(){
  const entries=historyEntries(),filter=$('history-filter').value,query=$('history-search').value.trim().toLocaleLowerCase();
  for(const key of selectedHistory)if(!entries.some(e=>e.key===key&&canBulkRemove(e)))selectedHistory.delete(key);
  if(['hidden','trash'].includes(filter))bulkMode=false;
  $('history-update').hidden=!online||snapshot.history_overrides!==undefined;
  const visible=entries.filter(e=>filter==='trash'?e.deleted:filter==='hidden'?e.hidden&&!e.deleted:!e.hidden&&!e.deleted&&(filter==='all'||filter===e.source)).filter(e=>!query||e.title.toLocaleLowerCase().includes(query));
  visibleHistory=visible;
  $('history-bulk').hidden=!bulkMode;$('history-selection-count').textContent='已选 '+selectedHistory.size;$('history-bulk-remove').disabled=bulkBusy||!online||!selectedHistory.size;
  $('history-bulk-cancel').disabled=bulkBusy;
  const eligible=visible.filter(canBulkRemove),checked=eligible.filter(e=>selectedHistory.has(e.key)).length;
  $('history-select-all').checked=eligible.length>0&&checked===eligible.length;$('history-select-all').indeterminate=checked>0&&checked<eligible.length;$('history-select-all').disabled=bulkBusy||!eligible.length;
  $('history-filter').querySelector('[value="hidden"]').textContent='已移出列表'+(entries.some(e=>e.hidden)?` (${entries.filter(e=>e.hidden).length})`:'');
  $('history-filter').querySelector('[value="trash"]').textContent='回收站'+(snapshot.trash?.length?` (${snapshot.trash.length})`:'');
  const html=visible.map(e=>`<div class="history-row ${e.selected?'selected':''} ${e.pinned?'pinned':''} ${selectedHistory.has(e.key)?'is-checked':''}" data-history-key="${e.key}">${bulkMode?`<input type="checkbox" class="history-select" data-history-select="${e.key}" aria-label="选择聊天：${escape(e.title)}" ${selectedHistory.has(e.key)?'checked':''} ${!canBulkRemove(e)||bulkBusy?'disabled':''}>`:''}<button type="button" class="history-item" data-history-open="${e.key}" data-tooltip="${escape(e.title)} · ${e.pinned?'已置顶 · ':''}${e.source==='panel'?'本面板':'其他窗口'} · ${escape(e.status)}" ${e.deleted?'aria-label="回收站聊天：'+escape(e.title)+'"':''}><span class="history-dot ${e.busy?'is-running':''}" aria-hidden="true">${e.pinned?'<svg class="history-pin" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="m10 2 4 4-1.6 1.6-1.1-.1-3.2 3.2v2.4l-2-2L2 15l3.9-4.1-2-2h2.4l3.2-3.2-.1-1.1Z"/></svg>':e.source==='external'?'↗':''}</span><span class="history-title">${escape(e.title)}</span></button><button class="history-more" type="button" data-history-more="${e.key}" aria-label="管理聊天：${escape(e.title)}" aria-haspopup="menu" data-tooltip="管理聊天" ${bulkMode?'hidden':''}>⋯</button></div>`).join('')||`<p class="history-empty">${query?'没有匹配的聊天':filter==='trash'?'回收站为空':filter==='hidden'?'没有移出的聊天':'暂无聊天'}</p>`;
  if(html!==historyRenderKey){$('history').innerHTML=html;historyRenderKey=html;}
  syncSelectPickers();
}
function canBulkRemove(entry){return !!entry&&!entry.deleted&&!entry.hidden&&!entry.busy;}
function toggleHistorySelection(entry){if(!canBulkRemove(entry)||bulkBusy)return;if(selectedHistory.has(entry.key))selectedHistory.delete(entry.key);else selectedHistory.add(entry.key);renderHistory();}
function renderBulkTargets(){
  const panel=bulkTargets.filter(e=>e.source==='panel').length,external=bulkTargets.length-panel;
  $('bulk-dialog-note').textContent=`共 ${bulkTargets.length} 条：${panel} 条本面板聊天移入回收站，${external} 条外部聊天移出列表。均可恢复；原始 CLI 记录保留。`;
  $('bulk-dialog-list').innerHTML=bulkTargets.map(e=>`<li>${escape(e.title)}<small>${e.source==='panel'?'移入回收站':'移出列表'}</small></li>`).join('');
}
function openBulkDialog(){bulkTargets=historyEntries().filter(e=>selectedHistory.has(e.key)&&canBulkRemove(e));if(!bulkTargets.length)return;bulkCompleted=0;closeSelectMenu();closeHistoryMenu();renderBulkTargets();$('bulk-dialog-error').hidden=true;$('bulk-dialog-confirm').disabled=false;$('bulk-dialog').showModal();}
async function removeBulkTargets(){
  if(bulkBusy)return;bulkBusy=true;$('bulk-dialog-confirm').disabled=true;$('bulk-dialog-cancel').disabled=true;$('bulk-dialog-error').hidden=true;
  const failed=[],removed=[];let skipped=0;
  try{
    for(let i=0;i<bulkTargets.length;i++){
      const target=bulkTargets[i],current=historyEntries().find(e=>e.key===target.key);
      if(!canBulkRemove(current)){selectedHistory.delete(target.key);skipped++;continue;}
      try{await api('/api/history',{source:target.source,id:target.id,action:target.source==='panel'?'delete':'hide'});removed.push(target);selectedHistory.delete(target.key);}
      catch(e){failed.push({target,error:e.message});if(!e.status||[403,404].includes(e.status)){for(const remaining of bulkTargets.slice(i+1))failed.push({target:remaining,error:'尚未处理'});break;}}
    }
    bulkCompleted+=removed.length;
    const removedCurrent=removed.some(e=>(viewMode==='panel'&&e.source==='panel'&&e.id===activeId)||(viewMode==='external'&&e.source==='external'&&e.id===externalId));
    await refresh();if(removedCurrent)showActivity();
    if(failed.length){bulkTargets=failed.map(e=>e.target);renderBulkTargets();$('bulk-dialog-error').textContent=`已移除 ${bulkCompleted} 条，${failed.length} 条未完成。${failed[0].error}`;$('bulk-dialog-error').hidden=false;}
    else{$('bulk-dialog').close();bulkMode=false;selectedHistory.clear();toast(`已移除 ${bulkCompleted} 条聊天，均可恢复${skipped?'；运行中或已变动的聊天已保留':''}`);}
  }finally{bulkBusy=false;$('bulk-dialog-confirm').disabled=false;$('bulk-dialog-cancel').disabled=false;renderHistory();}
}
function closeHistoryMenu(){$('history-menu').hidden=true;historyMenuEntry=null;}
function openHistoryMenu(entry,x,y){
  if(!entry)return;closeSelectMenu();historyMenuEntry=entry;
  $('history-menu-title').textContent=entry.title;
  $('history-menu-title').dataset.tooltip=entry.title;
  const actions=entry.deleted?['restore']:entry.hidden?['rename','unhide',...(entry.source==='panel'?['delete']:[])]:[entry.pinned?'unpin':'pin','rename',...(entry.canAssociate?['associate']:[]),'multiselect','hide',...(entry.source==='panel'?['delete']:[])];
  $('history-menu').querySelectorAll('[data-history-action]').forEach(button=>{const action=button.dataset.historyAction;button.hidden=!actions.includes(action);button.disabled=action==='multiselect'?bulkBusy||!visibleHistory.some(canBulkRemove):!online||(entry.busy&&entry.source==='panel'&&['delete','hide'].includes(action));});
  $('history-menu-note').textContent=entry.deleted?'可恢复对话及图片记录。':entry.busy&&entry.source==='panel'?'任务进行中，结束后可移除。':entry.source==='external'?'仅管理本面板显示，原窗口任务继续运行。':'删除后可在回收站恢复。';
  const menu=$('history-menu');menu.hidden=false;menu.style.left=Math.max(8,Math.min(x,window.innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,window.innerHeight-menu.offsetHeight-8))+'px';
  menu.querySelector('button:not([hidden]):not(:disabled)')?.focus();
}
async function applyHistoryAction(entry,action,title){
  if(['pin','unpin'].includes(action)){const next=loadHistoryPins();if(action==='pin')next.add(entry.key);else next.delete(entry.key);try{localStorage.setItem(historyPinsKey,JSON.stringify([...next]));}catch(e){throw new Error('无法保存置顶设置，请检查浏览器的本地存储权限。');}historyPins=next;render();toast(action==='pin'?'已置顶，刷新后仍会保留':'已取消置顶');return;}
  try{await api('/api/history',{source:entry.source,id:entry.id,action,...(title!==undefined?{title}: {})});}
  catch(e){if(e.status===404)throw new Error('管理功能已更新：请关闭控制台终端，再双击桌面的 Antigravity.command 并刷新网页。');throw e;}
  if(['delete','hide'].includes(action)&&entry.selected){if(entry.source==='panel'){activeId=null;sessionStorage.removeItem('agy-active');}showActivity();}
  await refresh();
  toast({rename:'已重命名',hide:'已移出列表，可在筛选中恢复',unhide:'已恢复到列表',delete:'已移入回收站，可恢复',restore:'已从回收站恢复'}[action]);
}
function requestHistoryAction(entry,action){
  closeHistoryMenu();
  if(action==='associate'){openSessionDialog('associate',entry);return;}
  if(action==='multiselect'){
    if(bulkBusy)return;closeSelectMenu();bulkMode=true;selectedHistory.clear();if(canBulkRemove(entry))selectedHistory.add(entry.key);renderHistory();$('history-select-all').focus();return;
  }
  if(['rename','delete'].includes(action)){
    historyDialogEntry=entry;historyDialogAction=action;
    const renaming=action==='rename';
    $('history-dialog-title').textContent=renaming?'重命名聊天':'删除这条聊天？';
    $('history-dialog-note').textContent=renaming?(entry.source==='external'?'修改在本面板显示的名称。':'为这条聊天设置一个容易找到的名称。'):`「${entry.title}」将移入回收站，可随时恢复。本操作保留官方 CLI 历史。`;
    $('history-name-label').hidden=!renaming;$('history-name').hidden=!renaming;$('history-name').required=renaming;$('history-name').value=entry.title;
    $('history-dialog-confirm').textContent=renaming?'保存':'移入回收站';$('history-dialog-confirm').classList.toggle('danger',!renaming);$('history-dialog-confirm').disabled=false;
    $('history-dialog-error').hidden=true;$('history-dialog').showModal();if(renaming){$('history-name').focus();$('history-name').select();}
  }else applyHistoryAction(entry,action).catch(e=>toast(e.message));
}
function openActivitySession(id){const conv=snapshot.conversations.find(c=>c.agy_id===id||Object.values(c.branches||{}).some(b=>b.agy_id===id));if(conv){const branch=Object.values(conv.branches||{}).find(b=>b.agy_id===id);selectConversation(conv.id,branch?.id);}else{const session=activityState.sessions.find(s=>s.id===id);if(session?.owner_conversation_id){selectConversation(session.owner_conversation_id,session.owner_branch);showRelatedSession(id);}else selectExternal(id);}}
function setSidebarOpen(open){if(!open){closeSelectMenu();if(bulkMode&&!bulkBusy&&!$('bulk-dialog').open){bulkMode=false;selectedHistory.clear();renderHistory();}}$('sidebar').classList.toggle('open',open);$('menu').setAttribute('aria-expanded',String(open));$('menu').dataset.tooltip=open?'收起菜单':'展开菜单';$('menu').setAttribute('aria-label',open?'收起菜单，扩大聊天区域':'展开菜单');}
const activityLabels={running:'正在运行',waiting:'等待操作',idle:'窗口打开 · 待命',ended:'进程已退出',unknown:'状态待确认'};
function revealHistorySource(source){if(!['all',source].includes($('history-filter').value)){$('history-filter').value='all';sessionStorage.setItem('agy-history-filter','all');}}
function setViewAddress(params){requestedCodexThread=null;const search=new URLSearchParams(params).toString();history.replaceState(null,'','/'+(search?'?'+search:''));}
function showHome(){const previous=stashComposerDraft();setViewAddress({});closeImagePreview();closeSelectMenu();closeHistoryMenu();accountExpanded=false;bulkMode=false;selectedHistory.clear();viewMode='home';externalDetail=null;restoreComposerDraft(previous);sessionStorage.setItem('agy-view',viewMode);$('task-kind').value='text';lastRender='';render();setSidebarOpen(false);startPolling();$('conversation').scrollTo({top:0,behavior:'instant'});}
function showActivity(){const previous=stashComposerDraft();setViewAddress({view:'activity'});closeImagePreview();accountExpanded=false;viewMode='activity';restoreComposerDraft(previous);$('task-kind').value='text';updateTaskMode();sessionStorage.setItem('agy-view',viewMode);lastRender='';render();setSidebarOpen(false);startPolling();}
function selectExternal(id){const previous=stashComposerDraft();setViewAddress({agy:id});closeImagePreview();accountExpanded=false;viewMode='external';externalId=id;externalDetail=null;restoreComposerDraft(previous);revealHistorySource('external');$('task-kind').value='text';updateTaskMode();sessionStorage.setItem('agy-view',viewMode);sessionStorage.setItem('agy-external',id);lastRender='';render();setSidebarOpen(false);startPolling();}
function renderActivity(){
  const bound=snapshot.conversations.filter(c=>c.codex_thread_id&&!historySettings('panel',c.id).hidden),boundIds=new Set(bound.flatMap(c=>[c.agy_id,...Object.values(c.branches||{}).map(b=>b.agy_id)]).filter(Boolean));
  const sessions=(activityState.sessions||[]).filter(s=>s.active||(!historySettings('external',s.id).hidden&&!snapshot.trash?.some(c=>c.agy_id===s.id)&&!snapshot.conversations.some(c=>c.agy_id===s.id&&historySettings('panel',c.id).hidden)));
  const live=sessions.filter(s=>s.active),running=live.filter(s=>['running','waiting'].includes(s.status)),others=activityState.unmatched_processes||[];
  const fingerprint=JSON.stringify([sessions,others,activityState.warning,activityOnline,snapshot.history_overrides,snapshot.trash,[...externalTitleCache],bound.map(c=>[c.id,c.title,c.agy_id,turnCount(c),c.turns.at(-1)?.status])]);
  if(activityFingerprint===fingerprint){const checked=$('activity-checked');if(checked)checked.textContent=accountCheckedLabel(activityState.checked_at)||'检查中';return;}
  activityFingerprint=fingerprint;
  const summary=`<div class="activity-intro"><span class="eyebrow">LOCAL AGY CLI · LIVE</span><h2>所有窗口，一处查看。</h2><p>这里是共享总览。选择固定聊天，进入对应的 Codex 与 Agy 对话。</p></div><div class="activity-stats"><div><strong>${live.length+others.length}</strong><span>活动会话 / 未关联进程</span></div><div><strong>${running.length}</strong><span>正在执行或等待操作</span></div><div><strong>${bound.length}</strong><span>已绑定 Codex 聊天</span></div></div>`;
  const owner=sessionStorage.getItem('agy-codex-owner');
  const fixed=bound.length?`<section class="activity-group"><h3>固定会话</h3><div class="bound-chats">${bound.map(c=>`<a class="bound-chat" data-bound-chat="${c.id}" href="/?codex=${encodeURIComponent(c.codex_thread_id)}"><span><strong>${escape(c.title)}</strong><small>${turnCount(c)} 轮对话 · ${escape(c.codex_thread_id.slice(0,8))}${c.codex_thread_id===owner?' · 上次固定会话':''}</small></span><b>${c.turns.some(t=>isBusy(t.status))?'处理中':'已绑定'}</b><i aria-hidden="true">↗</i></a>`).join('')}</div></section>`:'<p class="activity-empty">从 Codex 首次派发 Agy 任务后，对应固定会话会显示在这里。</p>';
  const cards=list=>list.filter(s=>s.active||(!historySettings('external',s.id).hidden&&!snapshot.trash?.some(c=>c.agy_id===s.id)&&!snapshot.conversations.some(c=>c.agy_id===s.id&&historySettings('panel',c.id).hidden))).map(s=>`<button class="session-card status-${s.status}" data-session="${s.id}"><div class="session-card-top"><strong>${escape(snapshot.conversations.find(c=>c.agy_id===s.id)?.title||externalTitle(s))}</strong><span>${activityLabels[s.status]||'状态待确认'}</span></div><p>${escape(s.workspace||'工作目录未记录')}</p><div><span>${escape(s.origin)}${s.parent_id?' · 子会话':''}</span><span>${escape((s.processes||[]).map(p=>`${p.window_label} · PID ${p.pid}`).join('，')||'历史会话')}</span><time>${escape(timeLabel(s.updated_at))}</time></div></button>`).join('');
  const unlinked=others.length?`<div class="activity-group"><h3>尚未关联对话的 CLI</h3>${others.map(p=>`<div class="unmatched-process"><strong>${escape(p.window_label)}</strong><span>PID ${p.pid} · ${escape(p.origin)} · 已运行 ${escape(p.elapsed)}</span><small>CLI 已打开；产生会话后会自动显示对话。部分旧版 CLI 不提供活动锁。</small></div>`).join('')}</div>`:'';
  const unboundLive=live.filter(s=>!boundIds.has(s.id)&&!s.owner_codex_thread_id&&!s.related&&!s.owner_deleted),ended=sessions.filter(s=>!s.active&&!boundIds.has(s.id)&&!s.owner_codex_thread_id&&!s.related&&!s.owner_deleted);
  $('activity-overview').innerHTML=summary+fixed+(!activityOnline?'<div class="activity-warning">活动同步已断开，显示上次记录。</div>':'')+(activityState.warning?`<div class="activity-warning">${escape(activityState.warning)}</div>`:'')+(unboundLive.length?`<div class="activity-group"><h3>其他活动窗口</h3>${cards(unboundLive)}</div>`:'')+unlinked+(ended.length?`<div class="activity-group"><h3>其他窗口的最近对话</h3>${cards(ended)}</div>`:'')+`<p class="activity-footnote">${escape(activityState.detail||'只读监控，不会启动模型。')}<br>最近同步：<time id="activity-checked">${escape(accountCheckedLabel(activityState.checked_at)||'检查中')}</time></p>`;
}

function renderQueue(){
  const supported=snapshot.service?.features?.queue_priority===true;
  const button=$('queue-toggle'),counts=queueSummary();
  button.disabled=reloadBusy||!online||!supported||snapshot.service?.stopping;
  button.textContent=reloadBusy?'队列 · 重载中':!online?'队列 · 未连接':snapshot.service?.stopping?'队列 · 正在关闭':!supported?'队列 · 待更新':counts.running||counts.queued?`队列 · ${counts.running} 执行 · ${counts.queued} 排队`:'队列 · 待命';
  button.dataset.tooltip=supported?queueStatusTooltip():'任务队列需要载入新版后台';
  if(!$('task-queue-dialog').open)return;
  const queue=snapshot.queue||{running:[],queued:[],version:''};
  $('task-queue-summary').textContent=`${queue.running.length} 项执行 · ${queue.queued.length} 项排队 · 同一会话保持先后`;
  const row=(t,queued)=>`<article class="queue-row"><div class="queue-position">${queued?t.position:'●'}</div><div class="queue-info"><strong>${escape(t.title)}</strong><small>${escape(t.session_title)} · ${escape(t.model)}</small><p>${escape(t.prompt)}</p></div><div class="queue-actions"><button type="button" data-queue-open="${t.id}">查看</button>${queued?`<button type="button" data-queue-move="up" data-queue-turn="${t.id}" data-queue-version="${queue.version}" aria-label="前移任务：${escape(t.prompt)}" ${queuePending||!online||!t.can_up?'disabled':''}>↑</button><button type="button" data-queue-move="down" data-queue-turn="${t.id}" data-queue-version="${queue.version}" aria-label="后移任务：${escape(t.prompt)}" ${queuePending||!online||!t.can_down?'disabled':''}>↓</button><button type="button" data-queue-cancel="${t.id}" ${queuePending||!online?'disabled':''}>取消</button>`:''}</div></article>`;
  const html=queue.running.map(t=>row(t,false)).join('')+queue.queued.map(t=>row(t,true)).join('')||'<p class="queue-empty">当前没有执行或排队任务。</p>';
  if($('task-queue-list').innerHTML!==html)$('task-queue-list').innerHTML=html;
}
async function openQueue(){if(!online||!snapshot.service?.features?.queue_priority)return;hideQuickTooltip();$('task-queue-dialog').showModal();renderQueue();await refresh();}
async function queueAction(event){
  const button=event.target.closest('[data-queue-move],[data-queue-cancel],[data-queue-open]');if(!button||queuePending)return;
  const id=button.dataset.queueTurn||button.dataset.queueCancel||button.dataset.queueOpen;
  if(button.dataset.queueOpen){const row=[...(snapshot.queue?.running||[]),...(snapshot.queue?.queued||[])].find(t=>t.id===id);if(row){$('task-queue-dialog').close();selectConversation(row.conversation_id,row.branch);}return;}
  queuePending=true;renderQueue();
  try{if(button.dataset.queueCancel)await api('/api/turns/'+id+'/cancel',{});
    else snapshot.queue=await api('/api/queue/reorder',{turn_id:id,direction:button.dataset.queueMove,version:button.dataset.queueVersion});
    await refresh();
  }catch(e){toast(e.message);await refresh();}finally{queuePending=false;renderQueue();}
}
async function loadUpdatedPage(){
  stashComposerDraft();
  if(draftPersisted||!$('prompt').value){location.reload();return true;}
  const response=await fetch('/',{cache:'no-store'}),page=new DOMParser().parseFromString(await response.text(),'text/html');
  const next=page.querySelector('meta[name="console-token"]')?.content;if(next)token=next;
  toast('后台已更新。草稿未能保存，请复制输入后手动刷新页面。');return false;
}
async function reloadService(){
  if(reloadBusy||!snapshot.service?.can_reload)return;
  stashComposerDraft();if($('prompt').value&&!draftPersisted){toast('请先复制草稿，浏览器暂时无法保存。');return;}
  const previous=snapshot.service.instance_id;reloadBusy=true;stopPolling();render();
  try{await api('/api/service/reload',{});
    $('settings-dialog').close();
    for(let attempt=0;attempt<30;attempt++){
      await new Promise(resolve=>setTimeout(resolve,700));
      try{const response=await fetch('/api/service',{cache:'no-store'});if(!response.ok)continue;const service=await response.json();if(service.instance_id!==previous){await loadUpdatedPage();return;}}catch(e){}
    }
    toast('尚未确认重载完成；草稿已保留，请检查原终端后刷新。');
  }catch(e){toast(e.message);}finally{reloadBusy=false;startPolling();render();}
}
function taskErrorHTML(error,evidence=null){
  if(!error)return '';
  if(evidence?.source==='official_tool_denial')return `<div class="error-box"><strong>工具权限被拒绝</strong><p>本轮未完成，请检查所需工具的授权。CLI 附带了旧会话的额度报错，已隔离；本轮未因此切换模型或重跑。</p><details><summary>查看诊断详情</summary><pre>${escape(error)}\n\n旧额度诊断（历史步骤 ${Number(evidence.historical_error_step)||0}）：\n${escape(evidence.raw_cli_error||'')}</pre></details></div>`;
  if(/soft.denied|auto-denied|permission denied|headless mode cannot prompt/i.test(error))return `<div class="error-box"><strong>所需工具需要授权</strong><p>Agy 未获准执行所需工具，本次任务未完成。可由当前 Codex 执行工具后交给 Agy 复核。</p><details><summary>查看原始错误</summary><pre>${escape(error)}</pre></details></div>`;
  if(/\b503\b|No capacity available/i.test(error))return `<div class="error-box"><strong>模型暂时繁忙（503）</strong><p>服务端暂无可用容量；这与账号额度不同。稍后重试，或选择其他可用模型。</p><details><summary>查看原始错误</summary><pre>${escape(error)}</pre></details></div>`;
  if(/i\/o timeout|connection (?:timeout|reset)|deadline exceeded/i.test(error))return `<div class="error-box"><strong>连接模型服务超时</strong><p>本轮未确认成功。请检查当前代理与网络，稳定后再重试。</p><details><summary>查看原始错误</summary><pre>${escape(error)}</pre></details></div>`;
  return `<div class="error-box">${escape(error)}</div>`;
}
function queueSummary(turn){
  const tasks=(snapshot.conversations||[]).flatMap(c=>c.turns||[]),running=snapshot.queue?.running.length??tasks.filter(t=>['running','cancelling'].includes(t.status)).length,queued=snapshot.queue?.queued||tasks.filter(t=>t.status==='queued').sort((a,b)=>Date.parse(a.created_at)-Date.parse(b.created_at));
  const index=queued.findIndex(t=>t.id===turn?.id);
  return {running,queued:queued.length,label:index>=0?`排队第 ${index+1} 位 · ${running} 项执行中；结束后自动开始`:'等待调度'};
}
function providerAvatarHTML(provider){
  const icons={
    codex:'<img src="data:image/png;base64,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" width="28" height="28" alt="" draggable="false">',
    muse:'<img src="data:image/png;base64,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" width="28" height="28" alt="" draggable="false">',
    claude:'C'
  };
  return icons[provider]?`<span class="provider-avatar provider-avatar--${provider}" aria-hidden="true">${icons[provider]}</span>`:'';
}
const expandedCodexPrompts=new Map();
function rememberCodexPrompt(id,open){
  if(typeof id!=='string'||!/^[a-f0-9]{32}$/.test(id))return;
  expandedCodexPrompts.delete(id);expandedCodexPrompts.set(id,!!open);
  while(expandedCodexPrompts.size>128)expandedCodexPrompts.delete(expandedCodexPrompts.keys().next().value);
}
function codexPromptHTML(turn){
  const text=String(turn.prompt||'');
  if(text.length<=280&&text.split('\n').length<=6)return escape(text);
  const preview=text.split('\n').filter(line=>line.trim()).slice(0,3).join(' ').slice(0,170);
  return `<details class="dispatch-details" data-dispatch-turn="${escape(turn.id)}"${expandedCodexPrompts.get(turn.id)?' open':''}><summary><span class="dispatch-chevron" aria-hidden="true">›</span><span class="dispatch-preview">${escape(preview)}${text.length>preview.length?'…':''}</span><span class="dispatch-expand-hint">完整指令</span></summary><div class="dispatch-full-text">${escape(text)}</div></details>`;
}
function panelUserHTML(t){
  const codex=t.source==='codex';
  return `<article class="message ${codex?'assistant codex-dispatch':'user'}"${codex?' data-provider="codex"':''}><div class="message-label">${codex?providerAvatarHTML('codex')+'Codex <span class="dispatch-turn-label">任务派发</span>':'你'} <time>${escape(timeLabel(t.created_at))}</time></div><div class="message-body">${codex?codexPromptHTML(t):escape(t.prompt)}</div></article>`;
}
function panelRoundtableHTML(message){
  const provider={codex:'Codex',muse:'Muse',claude:'Claude'}[message.provider],stage={proposal:'提案',review:'互评',summary:'总结'}[message.stage],source=roundtableSourceURL(message);
  return `<article class="message assistant roundtable-message" data-provider="${escape(message.provider)}"><div class="message-label">${providerAvatarHTML(message.provider)}${provider}<span class="roundtable-turn-label">圆桌 · ${message.role==='host'?'主持':'参与'} · ${stage}</span><time data-tooltip="发言同步到面板的时间">${escape(timeLabel(message.created_at))}</time></div><div class="message-body markdown">${markdown(message.text,'roundtable-'+message.id)}</div><div class="message-footer message-provenance"><span>Codex 同步</span>${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">原会话 ↗</a>`:''}<button class="copy-reply" data-roundtable-reply="${escape(message.id)}">复制发言</button></div></article>`;
}
const meetingDisclosureStorageKey='agy-meeting-disclosures-v1';
function validMeetingDisclosureKey(key){return typeof key==='string'&&/^[a-f0-9]{32}:[a-z:-]{1,64}$/.test(key);}
function loadMeetingDisclosures(){
  try{const entries=JSON.parse(sessionStorage.getItem(meetingDisclosureStorageKey)||'[]');return new Map(Array.isArray(entries)?entries.filter(item=>Array.isArray(item)&&item.length===2&&validMeetingDisclosureKey(item[0])&&typeof item[1]==='boolean').slice(-512):[]);}catch(error){return new Map();}
}
const meetingDisclosureState=loadMeetingDisclosures();
function rememberMeetingDisclosure(id,part,open){
  if(typeof id!=='string'||!/^[a-f0-9]{32}$/.test(id)||typeof part!=='string'||!/^[a-z:-]{1,64}$/.test(part))return;
  const key=id+':'+part;meetingDisclosureState.delete(key);meetingDisclosureState.set(key,!!open);while(meetingDisclosureState.size>512)meetingDisclosureState.delete(meetingDisclosureState.keys().next().value);
  try{sessionStorage.setItem(meetingDisclosureStorageKey,JSON.stringify([...meetingDisclosureState]));}catch(error){}
}
function meetingDisclosureOpen(id,part,fallback=false){const key=id+':'+part;return meetingDisclosureState.has(key)?meetingDisclosureState.get(key):fallback;}
function meetingTextHTML(meeting,slot,prefix){
  if(!slot.text)return '';
  const body='<div class="meeting-slot-text markdown">'+markdown(slot.text,prefix)+'</div>';
  if(slot.text.length<=(slot.stage==='summary'?360:600)&&slot.text.split('\n').length<=10)return body;
  const part='text:'+slot.provider+':'+slot.stage,preview=slot.text.replace(/\s+/g,' ').slice(0,140);
  return `<details class="meeting-text-fold" data-meeting-disclosure="${escape(meeting.id)}" data-meeting-part="${part}"${meetingDisclosureOpen(meeting.id,part)?' open':''}><summary><span>${escape(preview)}…</span><small>完整发言</small></summary>${body}</details>`;
}
function meetingMetricsHTML(meeting){
  const metrics=meeting.metrics;if(!metrics||typeof metrics.wall_seconds!=='number'||!Number.isFinite(metrics.wall_seconds)||metrics.wall_seconds<0)return '';
  const usage=metrics.usage,known=Number.isInteger(metrics.known_turn_count)?metrics.known_turn_count:0,total=usage&&typeof usage.total_tokens==='number'&&Number.isFinite(usage.total_tokens)&&usage.total_tokens>=0?usage.total_tokens:null;
  const unknown=Array.isArray(metrics.unknown_providers)?metrics.unknown_providers.filter(p=>['muse','claude','codex'].includes(p)).map(p=>({muse:'Muse',claude:'Claude',codex:'Codex'}[p])):[];
  return `<div class="meeting-metrics"><span data-meeting-wall="${escape(meeting.id)}" data-tooltip="从本场创建到结束或当前的真实墙钟时间；并行发言不相加">整场 ${escape(duration(metrics.wall_seconds))}</span><span data-tooltip="仅合计官方 CLI 可确认的本轮用量；网页没有提供的用量不估算">${meeting.participants.includes('agy')?'Agy ':''}${total===null?'用量未提供':Number(total).toLocaleString()+' tokens'}${known?' · '+known+' 轮':''}</span>${unknown.length?`<span>${escape(unknown.join('、'))} 用量未知</span>`:''}${metrics.unmeasured_turn_count?'<span>另有 '+Number(metrics.unmeasured_turn_count)+' 轮未计量</span>':''}</div>`;
}
function meetingRenderSignature(meeting){return JSON.stringify(meeting,(key,value)=>['wall_seconds','measured_at','wait_seconds','queue_seconds','run_seconds'].includes(key)?undefined:value);}
function meetingWallSeconds(meeting,now=Date.now()){
  const metrics=meeting.metrics;if(!metrics||typeof metrics.wall_seconds!=='number'||!Number.isFinite(metrics.wall_seconds)||metrics.wall_seconds<0)return null;
  if(metrics.ended_at||meeting.status!=='active')return metrics.wall_seconds;
  const measured=Date.parse(metrics.measured_at);return metrics.wall_seconds+(Number.isFinite(measured)?Math.max(0,(now-measured)/1000):0);
}
function updateMeetingClocks(conv,now=Date.now()){
  if(viewMode!=='panel'||!conv?.id||typeof renderedMessageRows==='undefined')return;
  for(const meeting of roundtableMeetings(conv)){
    const row=renderedMessageRows.get('meeting:'+meeting.id)?.element;if(!row)continue;
    const write=(node,value)=>{if(node&&node.textContent!==value)node.textContent=value;},wall=meetingWallSeconds(meeting,now);
    if(wall!==null)write(row.querySelector('[data-meeting-wall]'),'整场 '+duration(wall));
    for(const slot of meeting.slots){const clock=row.querySelector('[data-meeting-clock="'+slot.provider+':'+slot.stage+'"]');if(clock)write(clock,meetingSlotClock(slot,now));}
  }
}
function meetingSlotUsageHTML(slot){
  const parts=[];if(slot.provider==='agy'&&typeof slot.model==='string'&&slot.model)parts.push(slot.model);
  const usage=slot.usage,total=usage&&typeof usage.total_tokens==='number'&&Number.isFinite(usage.total_tokens)&&usage.total_tokens>=0?usage.total_tokens:null;
  if(slot.provider==='agy'&&total!==null)parts.push((slot.usage_scope==='turn'?'本轮 ':slot.usage_scope==='conversation'?'会话累计 ':'官方统计 ')+Number(total).toLocaleString()+' tokens');
  else if(['muse','claude'].includes(slot.provider)&&slot.status!=='pending')parts.push('网页用量未知');
  return parts.length?'<span class="meeting-slot-usage">'+parts.map(escape).join(' · ')+'</span>':'';
}
function meetingActionMarkup(meeting,slot){
  if(typeof snapshot==='undefined'||snapshot.service?.features?.roundtable_actions!==true)return '';
  return `<button type="button" class="copy-reply" data-meeting-action="inspect_result" data-meeting-id="${escape(meeting.id)}" data-meeting-provider="${escape(slot.provider)}" data-meeting-stage="${escape(slot.stage)}">核对结果</button>${slot.can_mark_unavailable===true&&slot.status==='pending'?`<button type="button" class="copy-reply" data-meeting-action="mark_unavailable" data-meeting-id="${escape(meeting.id)}" data-meeting-provider="${escape(slot.provider)}" data-meeting-stage="${escape(slot.stage)}">标记缺席</button>`:''}`;
}
let meetingActionState=null;
const meetingActionReceipts=new Map();
function meetingActionTarget(meetingId,provider,stage){
  const conv=active(),meeting=roundtableMeetings(conv).find(item=>item.id===meetingId),slot=meeting?.slots.find(item=>item.provider===provider&&item.stage===stage);
  return conv?.codex_thread_id&&slot?{cid:conv.id,thread:conv.codex_thread_id,key:pageCacheKey(conv.id),meeting,slot}:null;
}
function mergeMeetingResult(target,value){
  const valid=roundtableMeetings({codex_thread_id:target.thread,page:{branch:target.meeting.branch},roundtable_meetings:[value]});
  if(valid.length!==1||valid[0].id!==target.meeting.id)throw new Error('返回的圆桌记录不匹配，已保留原记录。');
  const entry=conversationPages.get(target.key);if(!entry?.value||entry.value.codex_thread_id!==target.thread)return;
  const merge=items=>(items||[]).map(item=>item.id===value.id&&Date.parse(item.updated_at)<=Date.parse(value.updated_at)?value:item);
  entry.value={...entry.value,roundtable_meetings:merge(entry.value.roundtable_meetings)};if(entry.meetingHistory)entry.meetingHistory.meetings=merge(entry.meetingHistory.meetings);
}
function paintMeetingAction(state){
  if(meetingActionState!==state)return;
  $('meeting-action-title').textContent=state.action==='mark_unavailable'?'确认未发送者缺席':'核对本场结果';
  $('meeting-action-status').textContent=state.target.slot.provider.toUpperCase()+' · '+({proposal:'提案',review:'互评',summary:'总结'}[state.target.slot.stage])+' · '+state.target.meeting.topic;
  $('meeting-action-confirm-area').hidden=state.action!=='mark_unavailable';$('meeting-action-submit').hidden=state.action!=='mark_unavailable';
  $('meeting-action-submit').disabled=state.pending;$('meeting-action-submit').textContent=state.pending?'保存中…':state.receipt?.uncertain?'重交同一确认':'确认缺席并继续';
  $('meeting-action-reason').disabled=state.pending||!!state.receipt;$('meeting-action-ack').disabled=state.pending||!!state.receipt;
  $('meeting-action-note').textContent=state.action==='mark_unavailable'?'仅适用于尚未发送的发言。已有发言保留，后续阶段由活跃的主持继续；此操作不调用模型。':'核对面板已有记录；网页回复仍需打开原会话确认。不会重发任务。';
  $('meeting-action-error').hidden=!state.error;$('meeting-action-error').textContent=state.error||'';
}
async function openMeetingAction(meetingId,provider,stage,action){
  const target=meetingActionTarget(meetingId,provider,stage);if(!online||!target||!['inspect_result','mark_unavailable'].includes(action)||typeof snapshot==='undefined'||snapshot.service?.features?.roundtable_actions!==true)return;
  if(action==='mark_unavailable'&&(target.slot.can_mark_unavailable!==true||target.slot.status!=='pending'))return;
  if(meetingActionState?.pending)return;
  const receipt=meetingActionReceipts.get(meetingId+':'+provider+':'+stage),state={target,action,pending:false,error:'',receipt};meetingActionState=state;
  $('meeting-action-reason').value=receipt?.reason||'';$('meeting-action-ack').checked=!!receipt;$('meeting-action-detail').replaceChildren();paintMeetingAction(state);$('meeting-action-dialog').showModal();
  if(action==='inspect_result'){
    state.pending=true;paintMeetingAction(state);
    try{
      const result=await api('/api/conversations/'+target.cid+'/meetings/'+meetingId+'/actions',{codex_thread_id:target.thread,action,provider,stage});
      if(meetingActionState!==state||!$('meeting-action-dialog').open)return;
      if(result.model_started!==false||result.slot?.provider!==provider||result.slot?.stage!==stage)throw new Error('核对返回不匹配。');mergeMeetingResult(target,result.meeting);
      const inspection=result.inspection||{},source=roundtableSourceURL({...result.slot,source_url:inspection.source_url||result.slot.source_url});
      $('meeting-action-detail').innerHTML='<p>'+escape(inspection.detail||'已读取本场保存的结果。')+'</p><p>记录状态：'+escape(result.slot.status)+'</p>'+(source?'<a href="'+escape(source)+'" target="_blank" rel="noopener noreferrer">打开原会话核对 ↗</a>':'')+(inspection.source_kind==='native_cli'&&inspection.turn_id?'<button type="button" class="meeting-focus-button" data-meeting-original="'+escape(meetingId)+'" data-meeting-turn="'+escape(inspection.turn_id)+'">查看官方 CLI 原始任务 ↗</button>':'');
    }catch(error){if(meetingActionState===state)state.error=error.message||'暂时无法核对，请保留原会话。';}
    finally{if(meetingActionState===state){state.pending=false;paintMeetingAction(state);}}
  }
}
async function confirmMeetingUnavailable(event){
  event?.preventDefault();const state=meetingActionState;if(!state||state.action!=='mark_unavailable'||state.pending||!online)return;
  const {target}=state,current=meetingActionTarget(target.meeting.id,target.slot.provider,target.slot.stage);
  if(!current||current.cid!==target.cid||current.thread!==target.thread||current.slot.status!=='pending'||current.slot.can_mark_unavailable!==true){state.error='该发言状态已经改变，请重新核对本场记录。';paintMeetingAction(state);return;}
  const reason=state.receipt?.reason||$('meeting-action-reason').value.trim();if(!reason||reason.length>1000||!$('meeting-action-ack').checked){state.error='请填写原因，并确认这条发言尚未发送。';paintMeetingAction(state);return;}
  const receipt=state.receipt||{source_id:'ui-absent-'+crypto.randomUUID(),reason,uncertain:false},receiptKey=target.meeting.id+':'+target.slot.provider+':'+target.slot.stage;
  state.receipt=receipt;meetingActionReceipts.delete(receiptKey);meetingActionReceipts.set(receiptKey,receipt);while(meetingActionReceipts.size>128)meetingActionReceipts.delete(meetingActionReceipts.keys().next().value);state.pending=true;state.error='';paintMeetingAction(state);
  try{
    const result=await api('/api/conversations/'+target.cid+'/meetings/'+target.meeting.id+'/actions',{codex_thread_id:target.thread,action:'mark_unavailable',provider:target.slot.provider,stage:target.slot.stage,source_id:receipt.source_id,reason:receipt.reason,acknowledge:true});
    if(result.model_started!==false||result.recorded!==true)throw new Error('保存结果尚不能确认，请核对本场记录。');mergeMeetingResult(target,result.meeting);meetingActionReceipts.delete(receiptKey);
    if(meetingActionState===state){$('meeting-action-dialog').close();meetingActionState=null;}toast(result.summary_ready?'缺席已记录 · 已可由主持总结':'缺席已记录 · 未启动模型');render();
  }catch(error){receipt.uncertain=!error.status||error.status>=500;if(!receipt.uncertain){meetingActionReceipts.delete(receiptKey);state.receipt=null;}state.error=receipt.uncertain?'保存结果待核对；保留同一确认编号，重交不会另发任务。':error.message;}
  finally{state.pending=false;if(meetingActionState===state)paintMeetingAction(state);}
}
function meetingSlotClock(slot,now=Date.now()){
  const read=value=>{const time=Date.parse(value);return Number.isFinite(time)?time:null;};
  const queued=read(slot.queued_at||slot.created_at),started=read(slot.started_at),ended=read(slot.ended_at),parts=[];
  if(queued!==null&&(slot.status==='queued'||started!==null))parts.push('排队 '+duration(Math.max(0,((started??ended??now)-queued)/1000)));
  if(started!==null)parts.push('执行 '+duration(Math.max(0,((ended??now)-started)/1000)));
  else if(['sending','waiting'].includes(slot.status)&&read(slot.wait_started_at)!==null)parts.push('等待 '+duration(Math.max(0,(now-read(slot.wait_started_at))/1000)));
  else if(typeof slot.wait_seconds==='number'&&Number.isFinite(slot.wait_seconds)&&slot.wait_seconds>=0)parts.push('等待 '+duration(slot.wait_seconds));
  else if(['sending','waiting'].includes(slot.status)&&read(slot.updated_at)!==null)parts.push('已等待 '+duration(Math.max(0,(now-read(slot.updated_at))/1000)));
  return parts.join(' · ');
}
function meetingSlotHTML(meeting,slot){
  const names={codex:'Codex',agy:'Agy',muse:'Muse',claude:'Claude'},host=meeting.host||meeting.roundtable?.host,labels={pending:'待发起',sending:'发送中 · 待确认',waiting:'等待回复',queued:'排队中',running:'执行中',cancelling:'正在停止',completed:'已回复',failed:'未完成',unavailable:'暂不可用',cancelled:'已取消',interrupted:'结果待核对'};
  const source=roundtableSourceURL(slot),clock=meetingSlotClock(slot),prefix='meeting-'+meeting.id+'-'+slot.provider+'-'+slot.stage;
  const body=meetingTextHTML(meeting,slot,prefix),detail=slot.detail||(slot.status==='sending'?'发送结果待核对；请打开原会话确认，暂勿重复发送。':'');
  return `<section class="meeting-slot meeting-slot--${escape(slot.status)}" data-meeting-provider="${escape(slot.provider)}" data-meeting-stage="${escape(slot.stage)}"><div class="meeting-slot-heading">${slot.provider==='agy'?'<img class="meeting-avatar" src="/icon.svg" alt="">':providerAvatarHTML(slot.provider)}<strong>${names[slot.provider]}</strong>${slot.provider===host?'<small class="meeting-host-label">主持</small>':''}<span class="meeting-slot-state">${labels[slot.status]}</span></div>${body}${detail?`<p class="meeting-slot-detail">${escape(detail)}</p>`:''}<div class="meeting-slot-meta"><span>${slot.provider==='agy'&&slot.turn_id?'官方 CLI 记录':slot.text?'Codex 已同步真实发言':slot.status==='pending'?'等待当前 Codex 组织':''}</span>${clock?`<time data-meeting-clock="${escape(slot.provider+':'+slot.stage)}">${escape(clock)}</time>`:''}${meetingSlotUsageHTML(slot)}${meetingActionMarkup(meeting,slot)}${source?`<a href="${escape(source)}" target="_blank" rel="noopener noreferrer">原会话 ↗</a>`:''}${slot.provider==='agy'&&slot.turn_id?`<button type="button" class="copy-reply" data-meeting-original="${escape(meeting.id)}" data-meeting-turn="${escape(slot.turn_id)}">原始任务 ↗</button>`:''}${slot.text?`<button type="button" class="copy-reply" data-meeting-copy="${escape(meeting.id)}" data-meeting-provider="${escape(slot.provider)}" data-meeting-stage="${escape(slot.stage)}">复制发言</button>`:''}</div></section>`;
}
function meetingProgressNote(meeting){
  if(meeting.status==='partial'){
    const names={codex:'Codex',agy:'Agy',muse:'Muse',claude:'Claude'},states={unavailable:'缺席',failed:'失败',cancelled:'取消',interrupted:'待核对'};
    const missing=[...new Set(meeting.slots.filter(slot=>states[slot.status]).map(slot=>names[slot.provider]+' '+states[slot.status]))];
    return '部分完成 · '+missing.join('、')+'；非全员共识，原因见各轮记录。';
  }
  if(meeting.status!=='active')return '';
  const stage=meeting.phase,slots=meeting.slots.filter(slot=>slot.stage===stage),count=slots.filter(slot=>slot.status==='completed').length;
  if(['proposal','review'].includes(stage)&&count<slots.length)return (stage==='proposal'?'提案':'互评')+'已收 '+count+'/'+slots.length+' 位 · 仅代表已发言方';
  return stage==='summary'?'两轮已结束，等待主持总结':'';
}
function meetingPartialDetail(meeting){
  return [['unavailable','缺席'],['failed','失败'],['cancelled','取消'],['interrupted','待核对']].map(([status,label])=>{const count=meeting.slots.filter(slot=>slot.status===status).length;return count?label+' '+count+' 项':'';}).filter(Boolean).join(' · ');
}
function meetingRoundHTML(meeting,stage,number,label){
  const slots=meeting.slots.filter(slot=>slot.stage===stage),order=['proposal','review','summary'],future=order.indexOf(stage)>order.indexOf(meeting.phase)&&slots.every(slot=>slot.status==='pending'),ended=slots.every(slot=>['completed','failed','unavailable','cancelled','interrupted'].includes(slot.status)),replied=slots.filter(slot=>slot.status==='completed').length;
  const heading=`<span class="meeting-round-number">${number}</span><span>${label}</span><small>${future?'待发起 · 前序结束后进行':replied+'/'+slots.length+' 已回复'}</small>`;
  if(future)return `<section class="meeting-round meeting-round--future" aria-label="${label}，等待前一阶段结束"><h3>${heading}</h3></section>`;
  if(stage!=='summary')return `<details class="meeting-round meeting-round-fold" data-meeting-disclosure="${escape(meeting.id)}" data-meeting-part="round:${stage}"${meetingDisclosureOpen(meeting.id,'round:'+stage,!ended)?' open':''}><summary>${heading}</summary><div class="meeting-round-slots">${slots.map(slot=>meetingSlotHTML(meeting,slot)).join('')}</div></details>`;
  return `<section class="meeting-round meeting-summary${ended?' meeting-summary--ready':''}"><h3>${heading}</h3><div class="meeting-round-slots">${slots.map(slot=>meetingSlotHTML(meeting,slot)).join('')}</div></section>`;
}
function panelMeetingHTML(meeting){
  const host=meeting.host||meeting.roundtable?.host,names={codex:'Codex',agy:'Agy',muse:'Muse',claude:'Claude'},states={active:'进行中',completed:'已完成',partial:'部分完成',cancelled:'已取消'},phases={proposal:'第一轮提案',review:'第二轮互评',summary:'主持总结',finished:'本场已结束'},summary=meeting.slots.find(slot=>slot.stage==='summary'),summaryFirst=summary?.status==='completed',stages=summaryFirst?[['summary','03','主持结论'],['proposal','01','独立提案'],['review','02','互评']]:[['proposal','01','独立提案'],['review','02','互评'],['summary','03','主持总结']];
  return `<article class="message meeting-card" data-meeting-id="${escape(meeting.id)}"><header class="meeting-heading"><div><span class="meeting-eyebrow">圆桌会议 · 主持 ${names[host]}</span><h2>${escape(meeting.topic)}</h2></div><div class="meeting-heading-actions"><span class="meeting-state meeting-state--${escape(meeting.status)}">${states[meeting.status]}</span><button type="button" class="meeting-focus-button" data-meeting-focus="${escape(meeting.id)}">只看本场</button></div></header><div class="meeting-overview"><span>${meeting.participants.map(p=>names[p]).join(' · ')}</span><span>${phases[meeting.phase]||'阶段待核对'}</span><time>${escape(timeLabel(meeting.created_at))}</time></div>${meetingMetricsHTML(meeting)}${meetingProgressNote(meeting)?`<p class="meeting-progress-note">${escape(meetingProgressNote(meeting))}</p>`:''}${stages.map(stage=>meetingRoundHTML(meeting,...stage)).join('')}<footer class="meeting-footer">${meeting.status==='completed'?'各方两轮回复和主持总结已记录':meeting.status==='partial'?(meetingPartialDetail(meeting)||'含未完成')+'；以实际已收到的发言为准':meeting.status==='cancelled'?'本场已取消，已有发言保留':'尚在组织中；开关与待发起状态不代表已调用'} · 记录 #${escape(meeting.id.slice(0,8))}</footer></article>`;
}
const expandedTaskProgress=new Map();
function rememberProgressExpansion(id,open){
  expandedTaskProgress.delete(id);expandedTaskProgress.set(id,open);
  while(expandedTaskProgress.size>128)expandedTaskProgress.delete(expandedTaskProgress.keys().next().value);
}
function taskProgressHTML(turn){
  const rows=Array.isArray(turn.progress)?turn.progress.slice(0,32):[],legacy=rows.length?[]:(turn.events||[]).slice(-12);
  if(!rows.length&&!legacy.length)return '';
  const states={active:'进行中',done:'已完成',ended:'本轮已结束',stopped:'已中断',failed:'未完成',cancelled:'已停止',interrupted:'未确认',skipped:'已跳过'},updated=turn.progress_updated_at;
  return `<details class="task-progress" data-progress-turn="${escape(turn.id)}"${expandedTaskProgress.get(turn.id)?' open':''}><summary>任务进度 <span>${escape(texts[turn.status]||turn.status)}${updated?' · '+escape(timeLabel(updated))+' 更新':''}</span></summary><ol>${rows.map(row=>`<li><span>${escape(row.label)}</span><small>${escape(states[row.state]||'已记录')}</small><time>${row.updated_at?escape(timeLabel(row.updated_at)):''}</time></li>`).join('')}${legacy.map(label=>`<li><span>${escape(label)}</span><small>旧记录</small></li>`).join('')}</ol>${turn.progress_omitted?`<p>已省略 ${Number(turn.progress_omitted)||0} 条较早步骤</p>`:''}${legacy.length?'<p>旧记录没有步骤时间。</p>':''}${isBusy(turn.status)?'<p>显示已收到的阶段；暂时没有新事件不代表任务失败。</p>':''}</details>`;
}
function modelChangesHTML(turn){
  const changes=Array.isArray(turn.model_changes)?turn.model_changes.slice(0,3):[],attempts=Array.isArray(turn.attempts)?turn.attempts.slice(0,2):[];
  if(!changes.length&&!turn.fallback_notice)return '';
  const blocked=turn.status==='failed'&&!!turn.fallback_notice;
  return '<details class="task-progress model-changes"'+(blocked?' open':'')+'><summary>'+(changes.length?'模型切换 · '+changes.length+' 次':'未自动切换 · 原因')+'</summary><ol>'+changes.map(c=>'<li><span>'+escape(c.from)+' → '+escape(c.to)+'<br>'+escape(c.reason)+'</span><time>'+escape(timeLabel(c.at))+'</time></li>').join('')+'</ol>'+(turn.fallback_notice?'<p>'+escape(turn.fallback_notice)+'</p>':'')+attempts.map((a,i)=>'<p>尝试 '+(i+1)+' · '+escape(a.model)+' · '+escape(texts[a.status]||a.status)+(a.error?' · '+escape(a.error):'')+'</p>').join('')+(attempts.length>1?'<p>各次用量保留；仅能确认本轮统计时才合计，累计统计不重复相加。</p>':'')+'</details>';
}
function completionStatusLabel(turn){
  if(turn.status==='completed'&&turn.completion_evidence){
    if(turn.completion_evidence.source==='official_recovered_api_error')return '重试后已回复';
    if(/Individual quota reached|quota (?:has been )?(?:exhausted|exceeded|depleted)/i.test(turn.completion_evidence.raw_cli_error||''))return '已完成 · 已排除旧额度错误';
  }
  return texts[turn.status]||turn.status;
}
function roundtableTurnLabel(turn){
  const value=turn?.roundtable;
  if(!value||value.mode!=='roundtable'||!['codex','agy','muse','claude'].includes(value.host)||!['host','participant'].includes(turn.roundtable_role)||value.rounds!==2)return '';
  return '<span class="roundtable-turn-label">圆桌 · '+(turn.roundtable_role==='host'?'Agy 主持':'Agy 参与')+' · 主持 '+roundtableHostLabel(value.host)+'</span>';
}
function panelAssistantHTML(t){return `<article class="message assistant"><div class="message-label"><img src="/icon.svg" alt="">Antigravity ${roundtableTurnLabel(t)}<time>${escape(t.model)}</time></div><div class="message-body markdown">${t.response?markdown(t.response,t.id):turnImages(t).length?'':`<div class="waiting-text"><span>●</span> ${isBusy(t.status)?'正在等待助手回复…':'没有收到回复'}</div>`}</div>${imageCards(t)}${taskErrorHTML(t.error,t.failure_evidence)}${taskProgressHTML(t)}${modelChangesHTML(t)}${t.review?`<details class="inline-review"><summary>${escape(reviewLabel(t.review))}</summary>${reviewMarkup(t.review,true)}</details>`:""}<div class="message-footer"><span class="status-${t.status}">${completionStatusLabel(t)}</span>${t.elapsed?`<span>· ${duration(t.elapsed)}</span>`:''}${t.usage?.total_tokens?`<span data-tooltip="${escape(tokenUsageText(t))}">· ${t.usage_scope==='turn'?'本轮 ':t.usage_scope==='conversation'?'会话累计 ':'CLI 统计 '}${Number(t.usage.total_tokens).toLocaleString()} tokens</span>`:''}${typeof taskActionMarkup==='function'?taskActionMarkup(t):''}${t.response?`<button class="copy-reply" data-reply="${t.id}">复制回复</button>`:''}</div></article>`;}

function renderExternal(position=null){
  const session=activityState.sessions.find(s=>s.id===externalId)||externalDetail?.session;
  $('conversation-title').textContent=session?externalTitle(session):'外部 CLI 会话';
  $('conversation-subtitle').textContent=`${activityLabels[session?.status]||'正在加载'} · ${session?.workspace||'其他窗口'}`;
  $('external-banner').textContent='正在查看其他窗口的对话。下方发送会另建一项任务；要续接这条对话，请回原窗口。关闭本面板不会停止原窗口的任务。';
  const messages=externalDetail?.messages||[];
  const viewport=$('conversation'),readingTop=position?.readingTop??viewport.scrollTop,nearBottom=position?.nearBottom??viewport.scrollHeight-readingTop-viewport.clientHeight<=48;
  const rows=messages.map(m=>({key:m.id,codePrefix:externalId+'-'+m.id,signature:JSON.stringify(m),html:()=>`<article class="message ${m.role==='user'?'user':'assistant'}"><div class="message-label">${m.role==='user'?'原窗口发送':'Antigravity'}<time>${escape(timeLabel(m.created_at))}</time></div><div class="message-body ${m.role==='user'?'':'markdown'}">${m.role==='user'?escape(m.text):m.text?markdown(m.text,externalId+'-'+m.id):'<p class="waiting-text">正在准备回复…</p>'}</div><div class="message-footer"><span>${m.status==='streaming'?'正在同步':'已同步'}</span>${m.truncated?'<span>内容较长，当前显示部分文本</span>':''}</div></article>`}));
  if(!rows.length)rows.push({key:'empty',signature:!!externalDetail,html:()=>'<p class="activity-empty">'+(externalDetail?'还没有可读取的对话消息。':'正在读取官方会话…')+'</p>'});
  if(externalDetail?.note)rows.push({key:'note',signature:externalDetail.note,html:()=>'<p class="sync-note" role="status">'+escape(externalDetail.note)+'</p>'});
  const changed=syncMessageRows('external:'+externalId,rows);lastRender='external:'+externalId;
  if(changed)viewport.scrollTo({top:nearBottom?viewport.scrollHeight:readingTop,behavior:'instant'});
}
async function refreshActivity(){if(activityPending)return;activityPending=true;let inventoryLoaded=false;try{activityState=await api('/api/activity');inventoryLoaded=true;activityOnline=true;if(viewMode==='external'&&externalId){const requested=externalId;const detail=await api(`/api/activity/sessions/${requested}`);if(externalId===requested)externalDetail=detail;}render();hydrateActivityTitles();}catch(e){activityOnline=false;if(e.status===404&&!inventoryLoaded)activityState.warning='服务尚未加载全局监控。请关闭原控制台终端，双击桌面的启动文件，然后刷新网页。';if(viewMode!=='panel')render();}finally{activityPending=false;}}
function selectConversation(id,branch=null){
  const conv=conversationMeta(id);if(!conv)return;const previous=stashComposerDraft();
  const saved=sessionStorage.getItem('agy-session:'+id);branch=conv.branches?.[branch]?branch:conv.branches?.[saved]?saved:conv.primary_branch||null;
  rememberFixedOwner(conv);const params=conv.codex_thread_id?{codex:conv.codex_thread_id}:{chat:id};if(branch&&branch!==conv.primary_branch)params.session=branch;setViewAddress(params);
  closeImagePreview();closeHistoryMenu();accountExpanded=false;relatedExpanded=false;viewMode='panel';activeId=id;activeBranch=branch;
  if(branch)sessionStorage.setItem('agy-session:'+id,branch);revealHistorySource('panel');sessionStorage.setItem('agy-view',viewMode);sessionStorage.setItem('agy-active',id);
  const key=pageCacheKey(id);restoreComposerDraft(previous);
  $('task-kind').value=restoredTaskKind(conv);updateTaskMode();lastRender='';
  const cached=conversationPages.get(key);if(cached){cached.params=branch?{branch}:{};cached.tag=null;cached.scroll='bottom';}
  render();loadConversationPage(id,branch?{branch}:{},true);setSidebarOpen(false);startPolling();
}
function renderComposer(conv,health){
  const currentTurn=composerTurn(conv);busy=!!currentTurn;
  const awaitingBinding=(!!requestedCodexThread&&!boundViewApplied)||(viewMode==='panel'&&!!conv?.summary);
  document.querySelector('.composer-wrap').hidden=false;
  const fixedTarget=viewMode!=='panel'&&conv?.codex_thread_id;
  $('composer-target').textContent=fixedTarget?'发送到固定聊天 · '+conv.title:viewMode==='external'?'发送后新建任务 · 当前外部对话保留在原窗口':viewMode==='activity'?'发送后新建任务':conv?.turns.length?'继续当前聊天':'发送到新任务';
  const session=conv?.branches?.[selectedBranch(conv)];if(session)$('composer-target').textContent=(fixedTarget?'发送到 '+conv.title+' · ':'发送到 ')+session.title;
  $('prompt').placeholder=conv?.turns.length?'继续和 Antigravity 一起工作…':'和 Antigravity 一起工作…';
  $('send').hidden=busy;$('stop').hidden=!busy;$('run-status').hidden=!busy;
  $('send').disabled=busy||submitting||modelSaving||awaitingBinding||!online||!health.connected||!$('prompt').value.trim();
  $('model').disabled=busy||submitting||awaitingBinding||modelSaving;$('task-kind').disabled=busy||submitting||awaitingBinding;$('prompt').disabled=busy||submitting||awaitingBinding;
  $('stop').disabled=!online||currentTurn?.status==='cancelling';syncComposerModelPickers();updateTaskMode();
  if(currentTurn){$('run-label').textContent=currentTurn.status==='cancelling'?'正在停止任务':currentTurn.status==='queued'?'任务已排队':'Antigravity 正在处理';$('run-detail').textContent=currentTurn.status==='cancelling'?'等待进程退出，保留已收到的内容…':currentTurn.events?.at(-1)==='正在处理'?'正在等待模型返回首段回复…':currentTurn.events?.at(-1)||'正在连接，首次回复可能需要片刻…';$('stop').dataset.turn=currentTurn.id;$('run-time').textContent=duration((Date.now()-new Date(currentTurn.started_at||currentTurn.created_at))/1000);}
  if(currentTurn?.status==='queued')$('run-detail').textContent=queueSummary(currentTurn).label;
}
function render(){
  // Capture before the composer/status bar changes the available reading height.
  const viewport=$('conversation'),readingTop=viewport.scrollTop,nearBottom=viewport.scrollHeight-readingTop-viewport.clientHeight<=48;
  memoryPollSync?.();
  const health=snapshot.health,conv=viewMode==='panel'?active():null;
  if($('fulltext-open'))$('fulltext-open').disabled=!online||!snapshot.service?.features?.history_search;
  document.title=(conv?.title||(viewMode==='external'?externalTitle(externalDetail?.session||{id:externalId,title:'Agy 会话'}):viewMode==='activity'?'全局活动':'研究工作台'))+' · Antigravity';
  document.body.classList.toggle('welcome-view',viewMode==='home'||(viewMode==='panel'&&!conv?.turns.length&&!roundtableMeetings(conv).length&&!conv?.meeting_summaries?.length));
  renderAccount();
  $('connection').className='connection '+(!online?'disconnected':health.checking?'checking':health.connected?'':'disconnected');
  $('connection').querySelector('span').textContent=!online?'服务已断开':health.checking?'检查 CLI…':health.connected?'CLI 已就绪':'CLI 待检查';
  $('connection').dataset.tooltip=!online?'本机控制台无法访问；请检查启动终端。':health.checking?'正在检查本机 CLI 与模型列表。':health.connected?`Antigravity ${health.version} · 本机控制台可访问，CLI 检查已通过；具体任务以实际返回为准。点击重新检查。`:health.error||'点击重新检查 CLI';
  $('offline-banner').hidden=online&&(health.connected||health.checking);
  $('offline-banner').textContent=!online?'终端控制台已断开，任务状态尚未确认。请双击「启动控制台.command」，然后刷新此页面。':health.error||'请检查 Antigravity 的登录和网络，然后点击右上角重新连接。';
  if(online&&requestedCodexThread&&!boundViewApplied){$('offline-banner').hidden=false;$('offline-banner').textContent=snapshot.service?.features?.codex_binding?'这条 Codex 聊天尚未建立 Agy 绑定。请从对应 Codex 聊天派发第一项任务。':'固定会话功能已更新。请等当前任务完成后，关闭并重开控制台终端，再刷新此链接。';}
  if(reloadBusy){$('offline-banner').hidden=false;$('offline-banner').textContent='正在原终端加载更新，草稿和固定会话会恢复。';}
  paintMemorySummary();if(quickTooltipTarget&&!$('quick-tooltip').hidden)paintQuickTooltip();
  $('service-reload').disabled=reloadBusy||!online||!snapshot.service?.can_reload;
  $('service-reload').textContent=reloadBusy?'重载中…':'重载更新';
  $('service-reload').dataset.tooltip=snapshot.service?.features?.foreground_reload?'空闲时在原终端加载更新，关闭终端仍会全部退出':'首次载入重载功能需重开原前台终端';
  renderQueue();
  renderModelSelection(health,conv);
  renderHistory();
  renderSessions();
  const activityCount=activityState.sessions.filter(s=>s.active).length+activityState.unmatched_processes.length;
  $('activity-count').textContent=String(activityCount);$('activity-nav').dataset.tooltip=`全局 CLI 活动 · ${activityCount} 个活动会话`;
  $('activity-nav').classList.toggle('active',viewMode==='activity');
  $('home-link').setAttribute('aria-current',viewMode==='home'?'page':'false');
  if(viewMode!=='panel'){$('meeting-focus-banner').hidden=true;$('meetings-paging').hidden=true;$('turns-paging').hidden=true;$('detail-error').hidden=true;}$('activity-overview').hidden=viewMode!=='activity';$('external-banner').hidden=viewMode!=='external';
  if(viewMode!=='external')externalDetail=null;
  if(viewMode!=='activity'&&$('activity-overview').childElementCount){$('activity-overview').replaceChildren();activityFingerprint='';}
  renderComposer(composerConversation(),health);
  renderModelQuota(health);
  renderSettings();
  if(viewMode==='home'){$('conversation-title').textContent='研究工作台';$('conversation-subtitle').textContent='选择任务，或直接开始聊天';$('empty').hidden=false;$('messages').hidden=true;$('export').disabled=true;if(lastRender!=='home'){clearMessageRows();lastRender='home';}return;}
  if(viewMode!=='panel'){$('empty').hidden=true;$('messages').hidden=viewMode!=='external';$('export').disabled=true;if(viewMode==='external')renderExternal({readingTop,nearBottom});else{$('conversation-title').textContent='全局 CLI 活动';$('conversation-subtitle').textContent='查看其他窗口的活动状态、请求和回复';if(lastRender!=='activity'){clearMessageRows();lastRender='activity';}renderActivity();}return;}
  $('conversation-title').textContent=conv?.title||'研究工作台';
  $('conversation-subtitle').textContent=conv?.codex_thread_id?'已绑定 Codex · '+collaborationStateText(conversationMeta(conv.id)):conv?.turns.length?`${turnCount(conv)} 轮对话 · 继续追问会保留上下文`:'任务、进度和结果，都在这里';
  $('export').disabled=!conv?.turns.length;
  const hasTranscript=!!conv?.turns.length||roundtableMeetings(conv).length>0||!!conv?.meeting_summaries?.length;$('empty').hidden=hasTranscript;$('messages').hidden=!hasTranscript;
  renderPaging(conv);renderMeetingPaging(conv);
  if(conv?.summary){syncMessageRows('loading:'+conv.id,[{key:'loading',signature:'loading',html:()=>'<p class="activity-empty">正在载入最近 20 轮对话…</p>'}]);return;}
  const rows=[],speeches=conv?.turns?.length?roundtableMessages(conv):[],meetings=Array.isArray(conv?.roundtable_meetings)?roundtableMeetings(conv):[],meetingTurns=meetings.length?meetingLinkedTurnIDs(meetings):new Set();for(const t of conv?.turns||[]){
    const grouped=t.meeting_id&&meetingTurns.has(t.id)&&meetings.some(meeting=>meeting.id===t.meeting_id&&meeting.slots.some(slot=>slot.provider==='agy'&&slot.turn_id===t.id));
    if(!grouped){rows.push({key:t.id+':user',timelineAt:t.created_at,signature:JSON.stringify([t.prompt,t.created_at,t.source]),html:()=>panelUserHTML(t)});
    rows.push({key:t.id+':assistant',timelineAt:t.created_at,codePrefix:t.id,signature:JSON.stringify([t.response,t.status,t.error,t.model,t.elapsed,t.usage,t.usage_scope,t.images,t.completion_evidence,t.failure_evidence,t.compaction_evidence,t.action,t.recovery_of,t.review,t.progress,t.progress_updated_at,t.progress_omitted,t.events,t.model_changes,t.fallback_notice,t.attempts,t.roundtable,t.roundtable_role,conversationMeta(conv.id)?.branches?.[t.branch]?.last_turn_id]),html:()=>panelAssistantHTML(t)});}
    for(const message of speeches)if(message.anchor_turn_id===t.id)rows.push({key:'roundtable:'+message.id,timelineAt:t.created_at,codePrefix:'roundtable-'+message.id,signature:JSON.stringify(message),html:()=>panelRoundtableHTML(message)});
  }
  for(const meeting of meetings)rows.push({key:'meeting:'+meeting.id,timelineAt:meeting.created_at,codePrefix:'meeting-'+meeting.id,signature:typeof meetingRenderSignature==='function'?meetingRenderSignature(meeting):JSON.stringify(meeting),html:()=>panelMeetingHTML(meeting)});
  if(meetings.length)rows.sort((a,b)=>Date.parse(a.timelineAt)-Date.parse(b.timelineAt));
  if(typeof filterFocusedMeetingRows==='function')filterFocusedMeetingRows(conv,rows);
  const changed=syncMessageRows('panel:'+conv?.id,rows);if(typeof updateMeetingClocks==='function')updateMeetingClocks(conv);lastRender='panel:'+conv?.id;
  const entry=conversationPages.get(pageCacheKey(conv?.id)),scroll=entry?.scroll;if(entry)entry.scroll=null;
  if(scroll?.startsWith('turn:')){
    const target=renderedMessageRows.get(scroll.slice(5))?.element;
    if(target){target.scrollIntoView({block:'center',behavior:'instant'});target.classList.add('search-hit');}
    else viewport.scrollTo({top:viewport.scrollHeight,behavior:'instant'});
  }else if(scroll||changed){
    const top=scroll==='top'?0:scroll==='bottom'||nearBottom&&!conv?.page?.has_newer?viewport.scrollHeight:readingTop;
    viewport.scrollTo({top,behavior:'instant'});
  }

}
function refresh(afterChange=true){
  if(stateRefreshPromise)return afterChange?stateRefreshPromise.then(()=>refresh(false)):stateRefreshPromise;
  stateRefreshPromise=(async()=>{
    try{
      const next=await api('/api/state?summary=1'),wasOnline=online;online=true;
      if(next===null){if(!followBoundActivity()&&!wasOnline)render();if(typeof updateMeetingClocks==='function')updateMeetingClocks(active());synchronizeDetail();return;}
      const instance=next.service?.instance_id||(next.service?.pid?'pid:'+next.service.pid:null);if(lastServiceInstance&&instance&&instance!==lastServiceInstance){await loadUpdatedPage();}if(instance)lastServiceInstance=instance;
      snapshot=next;if(typeof syncBackendOrder==='function')await syncBackendOrder();if(activeId&&!snapshot.conversations.some(c=>c.id===activeId))activeId=null;
      if(requestedCodexThread&&!boundViewApplied){const bound=snapshot.conversations.find(c=>c.codex_thread_id===requestedCodexThread);if(bound){activeId=bound.id;activeBranch=bound.branches?.[activeBranch]?activeBranch:bound.primary_branch||null;viewMode='panel';sessionStorage.setItem('agy-active',activeId);sessionStorage.setItem('agy-view',viewMode);boundViewApplied=true;}}
      if(!kindRestored){$('task-kind').value=viewMode==='panel'?restoredTaskKind(conversationMeta(activeId)):'text';kindRestored=true;}
      restoreFirstDraft();
      if(!followBoundActivity())render();synchronizeDetail();
    }catch(e){online=false;render();}
  })().finally(()=>{stateRefreshPromise=null;});
  return stateRefreshPromise;
}
async function newConversation(){try{let conv=fixedOwnerConversation();if(!conv){conv=await api('/api/conversations',{});snapshot.conversations.push(conv);}selectConversation(conv.id);$('prompt').value='';$('prompt').focus();render();}catch(e){toast(e.message);}}
$('new-task').addEventListener('click',newConversation);
function openSettings(){
  hideQuickTooltip();if(resourceExpanded)closeResources();
  if(!$('settings-dialog').open)$('settings-dialog').showModal();
  setSidebarOpen(false);renderSettings();loadConsoleStartup();if(settingsInsightsExpanded)readSettingsInsights();
}
$('settings-toggle').addEventListener('click',()=>openSettings());
$('settings-close').addEventListener('click',()=>$('settings-dialog').close());
$('settings-dialog').addEventListener('close',()=>{settingsInsights.sequence++;settingsInsights.pending=false;multiAIExpanded=false;consoleStartupExpanded=false;roundtableHostsExpanded=false;renderCollaborationControl(conversationMeta(activeId));});
$('settings-collaboration').addEventListener('click',()=>{const button=$('settings-collaboration'),conv=conversationMeta(button.dataset.conversation);if(conv?.codex_thread_id===button.dataset.thread)return setCollaborationPreference(conv,multiAIState(conv)?.enabled===false);});
$('multi-ai-expand').addEventListener('click',()=>{multiAIExpanded=!multiAIExpanded;renderSettings();});
for(const name of ['agy','muse','claude'])$('provider-'+name).addEventListener('click',()=>{const button=$('provider-'+name),conv=conversationMeta(button.dataset.conversation);if(conv?.codex_thread_id===button.dataset.thread)return setMultiAIPreference(conv,{providers:{[name]:{enabled:multiAIState(conv)?.providers[name].enabled===false}}});});
$('roundtable-host-toggle').addEventListener('click',()=>{if($('roundtable-host-toggle').disabled)return;roundtableHostsExpanded=!roundtableHostsExpanded;renderSettings();});
document.querySelectorAll('[data-roundtable-host]').forEach(button=>button.addEventListener('click',()=>{const control=$('roundtable-host-toggle'),conv=conversationMeta(control.dataset.conversation);if(conv?.codex_thread_id===control.dataset.thread)return setRoundtablePreference(conv,{host:button.dataset.roundtableHost});}));
$('roundtable-enabled').addEventListener('click',()=>{const control=$('roundtable-enabled'),conv=conversationMeta(control.dataset.conversation);if(conv?.codex_thread_id===control.dataset.thread)return setRoundtablePreference(conv,{mode:roundtableState(conv)?.mode==='roundtable'?'assist':'roundtable'});});
$('console-startup-expand').addEventListener('click',()=>{consoleStartupExpanded=!consoleStartupExpanded;renderSettings();if(consoleStartupExpanded)loadConsoleStartup();});
$('console-launcher-path').addEventListener('input',()=>{consoleLauncherDirty=true;});
$('console-launcher-save').addEventListener('click',saveConsoleLauncher);
$('settings-auto-start').addEventListener('click',()=>{const button=$('settings-auto-start'),conv=conversationMeta(button.dataset.conversation);if(conv?.codex_thread_id===button.dataset.thread)return setConsoleAutoStart(conv,conv?.auto_start_console!==true);});
$('settings-dialog').addEventListener('click',event=>{let changed=false;if(multiAIExpanded&&!event.target.closest('#multi-ai-section')){multiAIExpanded=false;changed=true;}if(consoleStartupExpanded&&!event.target.closest('#console-startup-section')){consoleStartupExpanded=false;changed=true;}if(changed)renderSettings();});
$('settings-auto-open').addEventListener('click',()=>{const conv=conversationMeta($('settings-auto-open').dataset.conversation);return setAutoOpenPreference(conv,conv?.auto_open_activity===false);});
document.querySelectorAll('[data-font-size]').forEach(b=>b.addEventListener('click',()=>saveUISetting('font',Number(b.dataset.fontSize))));
document.querySelectorAll('[data-idle-ms]').forEach(b=>b.addEventListener('click',()=>saveUISetting('idleMs',Number(b.dataset.idleMs))));
document.querySelectorAll('[data-theme-choice]').forEach(b=>b.addEventListener('click',()=>saveUISetting('theme',b.dataset.themeChoice)));
function renderGlobalModelSettings(){
  const models=orderedModels(snapshot.health?.models||[],uiSettings.modelOrder),selected=snapshot.preferred_model||'',source=$('global-model-select');
  let html='<option value="">智能选择（按额度）</option>'+models.map(m=>'<option value="'+escape(m.id)+'">'+escape(m.label)+'</option>').join('');
  if(selected&&!models.some(m=>m.id===selected))html+='<option disabled value="'+escape(selected)+'">'+escape(selected)+'（暂不可用）</option>';
  if(!modelSaving){if(source.innerHTML!==html)source.innerHTML=html;source.value=selected;}
  source.disabled=modelSaving||!online;
  splitModelSelection('global-model-select','global-model-family','global-model-speed');
  $('settings-model-label').textContent=modelSaving?'保存中…':'自动保存';
}
let updateActionPending=false;
function softwareUpdateNotice(service,connected){
  const update=service.updates||{},preferences=update.preferences||{},target=update.pending_version||update.latest?.version;
  const visible=!!(connected&&service.features?.software_updates&&preferences.repository&&target&&target!==service.version&&['available','downloading','staged','error'].includes(update.status));
  const labels={available:'有更新',downloading:'下载中',staged:'待重启',error:'有更新'};
  return {visible,target:target||'',label:labels[update.status]||'有更新'};
}
function softwareReleaseLinks(update){
  const repository=update.preferences?.repository||'';
  if(!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository))return {project:'',notes:''};
  const project='https://github.com/'+repository,releases=project+'/releases',latest=update.latest?.release_url;
  return {project,notes:typeof latest==='string'&&latest.startsWith(releases+'/tag/')?latest:releases};
}
function paintSoftwareUpdateLink(id,url){const node=$(id);node.hidden=!url;if(url)node.href=url;else node.removeAttribute('href');}
function renderSoftwareUpdates(){
  const element=$('software-version');if(!element)return;
  const service=snapshot.service||{},update=service.updates||{},preferences=update.preferences||{},supported=service.features?.software_updates===true;
  element.textContent=service.version?'v'+service.version:'';
  const notice=$('sidebar-update'),view=softwareUpdateNotice(service,online);
  notice.hidden=!view.visible;notice.dataset.state=view.visible?update.status:'hidden';
  notice.dataset.tooltip=view.visible?view.label+' · v'+view.target+'；点击查看更新详情。':'';
  notice.setAttribute('aria-label',view.label+'，v'+view.target+'，查看更新详情');$('sidebar-update-label').textContent=view.label;
  const labels={idle:'等待检查',checking:'正在检查…',downloading:'正在下载并校验…',current:'已是此平台最新版本',available:'可更新到 v'+(update.latest?.version||''),staged:'v'+(update.pending_version||'')+' 已就绪，下次启动应用',error:update.error||'检查失败'};
  const status=!online?'控制台未连接':!supported?'加载新版后台后可用':!preferences.repository?'发布源待配置':labels[update.status]||'等待检查';
  $('software-update-status').textContent=status;
  const busyUpdate=updateActionPending||['checking','downloading'].includes(update.status);
  $('software-update-check').disabled=!online||!supported||busyUpdate||!preferences.repository;
  const canDownload=view.visible&&['available','error'].includes(update.status)&&!!update.latest?.version;
  for(const id of ['software-update-download','update-notice-download']){const button=$(id);button.hidden=!canDownload;button.disabled=busyUpdate||!online||!!service.active_tasks;button.textContent=update.status==='error'?'重试下载':'下载更新';}
  $('update-notice-title').textContent=view.visible?update.status==='staged'?'更新已就绪':'发现新版本':'软件更新';
  $('update-notice-version').textContent=view.target?'v'+(service.version||'—')+' → v'+view.target:'v'+(service.version||'—');
  $('update-notice-status').textContent=status;
  const bytes=update.latest?.bytes;$('update-notice-size').textContent=typeof bytes==='number'&&bytes>0?(bytes/1024/1024).toFixed(1)+' MB':'';
  $('update-notice-hint').textContent=update.status==='staged'?'任务结束后，从原启动入口重开即可安装。':'空闲时下载并校验，下次启动安装；不打断任务。';
  const links=softwareReleaseLinks(update);paintSoftwareUpdateLink('software-project-link',links.project);paintSoftwareUpdateLink('software-release-notes',links.notes);paintSoftwareUpdateLink('update-notice-notes',links.notes);
  if(document.activeElement!==$('software-update-repo'))$('software-update-repo').value=preferences.repository||'';
  if(document.activeElement!==$('software-update-channel'))$('software-update-channel').value=preferences.channel||'stable';
  $('software-update-auto').checked=preferences.automatic===true;$('software-update-auto').disabled=busyUpdate||!online||!supported;
  $('software-update-source-save').disabled=busyUpdate||!online||!supported;
}
function openSoftwareUpdateNotice(){
  renderSoftwareUpdates();if($('sidebar-update').hidden)return;
  hideQuickTooltip();setSidebarOpen(false);if(resourceExpanded)closeResources();
  if(!$('software-update-dialog').open)$('software-update-dialog').showModal();
}
async function softwareUpdateAction(action,body){
  if(updateActionPending)return;updateActionPending=true;renderSoftwareUpdates();
  try{const value=await api('/api/updates/'+action,body);snapshot.service.updates=value;}
  catch(error){toast('更新：'+error.message);}
  finally{updateActionPending=false;renderSoftwareUpdates();}
}
$('software-update-check').addEventListener('click',()=>softwareUpdateAction('action',{action:'check'}));
$('software-update-download').addEventListener('click',()=>softwareUpdateAction('action',{action:'download'}));
$('sidebar-update').addEventListener('click',openSoftwareUpdateNotice);
$('update-notice-close').addEventListener('click',()=>$('software-update-dialog').close());
$('software-update-dialog').addEventListener('close',()=>{if(!$('sidebar-update').hidden)$('sidebar-update').focus();});
$('update-notice-download').addEventListener('click',()=>softwareUpdateAction('action',{action:'download'}));
$('software-update-source-save').addEventListener('click',()=>softwareUpdateAction('preferences',{repository:$('software-update-repo').value.trim(),channel:$('software-update-channel').value,automatic:$('software-update-auto').checked}));
$('software-update-auto').addEventListener('change',()=>softwareUpdateAction('preferences',{...(snapshot.service?.updates?.preferences||{}),automatic:$('software-update-auto').checked}));
async function saveGlobalModelChoice(){
  if(modelSaving||!online)return;
  const model=$('global-model-select').value,efforts=[...(globalEffortDraft?.values()||[]),model];
  if(model===(snapshot.preferred_model||'')){globalEffortDraft=null;renderGlobalModelSettings();return;}
  modelSaving=true;$('global-model-error').hidden=true;renderGlobalModelSettings();
  try{
    const saved=await api('/api/model',{model});
    if(saved.preferred_model!==model)throw Error('返回的模型与本次选择不一致');
    Object.assign(snapshot,saved);preferredModel=saved.preferred_model;lastServerModelStamp=saved.preferred_model_updated_at;
    if(model)rememberModelEfforts('global',efforts);
  }catch(error){$('global-model-error').textContent='未保存：'+error.message;$('global-model-error').hidden=false;}
  finally{modelSaving=false;globalEffortDraft=null;renderGlobalModelSettings();render();}
}
$('fallback-expand').addEventListener('click',()=>{fallbackExpanded=!fallbackExpanded;renderFallbackSettings();});
$('fallback-kind').addEventListener('change',renderFallbackSettings);
document.querySelectorAll('[data-fallback-kind]').forEach(button=>button.addEventListener('click',()=>{$('fallback-kind').value=button.dataset.fallbackKind;fallbackAvailableExpanded=false;renderFallbackSettings();}));
$('fallback-scope').addEventListener('change',()=>{fallbackScopeId=$('fallback-scope').value;renderFallbackSettings();});
$('fallback-enabled').addEventListener('click',()=>{const {settings}=fallbackView();saveFallbackSettings({...settings,enabled:!settings.enabled});});
$('fallback-reset').addEventListener('click',()=>{const {settings}=fallbackView();saveFallbackSettings({enabled:settings.enabled});});
$('fallback-add-toggle').addEventListener('click',()=>{fallbackAvailableExpanded=!fallbackAvailableExpanded;renderFallbackSettings();});
$('fallback-models').addEventListener('click',event=>{const button=event.target.closest('[data-fallback-move]');if(button&&!button.disabled)moveFallbackModel(button.dataset.fallbackId,button.dataset.fallbackMove);});
$('fallback-models').addEventListener('change',event=>{
  const input=event.target,family=input.dataset.fallbackFamily||input.dataset.fallbackTier;if(!family)return;
  const {owner,kind,settings}=fallbackView(),group=modelFamilies(orderedModels(snapshot.health?.models||[],uiSettings.modelOrder)).find(g=>g.key===family);if(!group)return;
  let models=[...fallbackCandidates(owner,kind,settings)],index=models.findIndex(m=>group.variants.some(v=>v.id===m));
  if(input.dataset.fallbackTier||input.checked){const select=[...$('fallback-models').querySelectorAll('[data-fallback-tier]')].find(n=>n.dataset.fallbackTier===family),value=input.dataset.fallbackTier?input.value:select?.value||group.variants[0].id;if(index<0)models.push(value);else models[index]=value;}
  else if(index>=0)models.splice(index,1);
  saveFallbackSettings({enabled:settings.enabled,models});
});
$('settings-insights-toggle').addEventListener('click',()=>{settingsInsightsExpanded=!settingsInsightsExpanded;renderSettingsInsights();if(settingsInsightsExpanded)readSettingsInsights();});
$('settings-insights-refresh').addEventListener('click',()=>readSettingsInsights(true));
installLightDismiss($('settings-dialog'));
installLightDismiss($('software-update-dialog'));
$('queue-toggle').addEventListener('click',openQueue);
$('task-queue-close').addEventListener('click',()=>$('task-queue-dialog').close());
$('task-queue-refresh').addEventListener('click',()=>refresh());
$('task-queue-list').addEventListener('click',queueAction);
$('service-reload').addEventListener('click',reloadService);
$('gpt-collaboration').addEventListener('click',openMultiAISettings);
$('collaboration-records').addEventListener('click',openCollaborationAudit);
$('collaboration-audit-close').addEventListener('click',()=>$('collaboration-audit-dialog').close());
$('collaboration-audit-refresh').addEventListener('click',()=>{$('collaboration-audit-detail').hidden=true;$('collaboration-audit-detail').replaceChildren();$('collaboration-audit-list').hidden=false;$('collaboration-audit-back').hidden=true;loadCollaborationAudit();});
$('collaboration-audit-older').addEventListener('click',()=>loadCollaborationAudit(true));
$('collaboration-audit-back').addEventListener('click',()=>{collaborationAudit.sequence++;$('collaboration-audit-detail').hidden=true;$('collaboration-audit-detail').replaceChildren();$('collaboration-audit-list').hidden=false;$('collaboration-audit-back').hidden=true;});
$('collaboration-audit-list').addEventListener('click',event=>{const button=event.target.closest('[data-collaboration-turn]');if(button)showCollaborationTurn(button.dataset.collaborationTurn);});
$('collaboration-audit-dialog').addEventListener('close',()=>{collaborationAudit.sequence++;collaborationAudit.pending=false;collaborationAudit.records=[];$('collaboration-audit-list').replaceChildren();$('collaboration-audit-detail').replaceChildren();});
document.addEventListener('pointerover',event=>{const target=event.target.closest('[data-tooltip]');if(target&&!target.contains(event.relatedTarget))scheduleQuickTooltip(target);});
document.addEventListener('pointerout',event=>{if(quickTooltipTarget&&!quickTooltipTarget.contains(event.relatedTarget))hideQuickTooltip();});
document.addEventListener('focusin',event=>{const target=event.target.closest('[data-tooltip]');if(target)scheduleQuickTooltip(target);});
document.addEventListener('focusout',hideQuickTooltip);
document.addEventListener('pointerdown',hideQuickTooltip,true);
window.addEventListener('blur',hideQuickTooltip);
window.addEventListener('resize',hideQuickTooltip);
document.addEventListener('scroll',hideQuickTooltip,true);
document.addEventListener('visibilitychange',()=>{if(document.hidden)hideQuickTooltip();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')hideQuickTooltip();},true);
$('session-tabs').addEventListener('click',event=>{const tab=event.target.closest('[data-session-open]');if(tab)selectConversation(activeId,tab.dataset.sessionOpen);});
$('session-add').addEventListener('click',()=>openSessionDialog());
$('session-rename').addEventListener('click',()=>openSessionDialog('rename'));
$('session-related-toggle').addEventListener('click',()=>{relatedExpanded=!relatedExpanded;renderSessions();});
$('session-related-list').addEventListener('click',event=>{const button=event.target.closest('[data-related-session]');if(button)showRelatedSession(button.dataset.relatedSession);});
$('session-dialog-cancel').addEventListener('click',()=>$('session-dialog').close());
$('session-dialog-form').addEventListener('submit',async event=>{
  event.preventDefault();if(!sessionDialogState||$('session-dialog-confirm').disabled)return;
  const state=sessionDialogState,cid=state.mode==='associate'?$('session-owner').querySelector(':checked')?.value:state.cid;
  if(!cid){$('session-dialog-error').textContent='请先选择固定入口。';$('session-dialog-error').hidden=false;return;}
  $('session-dialog-confirm').disabled=true;$('session-dialog-error').hidden=true;
  try{const branch=await api('/api/conversations/'+cid+'/branches',{title:$('session-name').value.trim(),...(state.key?{id:state.key}:{}),...(state.agy_id?{agy_id:state.agy_id}:{})});
    $('session-dialog').close();await refresh();rememberSessionResult(cid,branch);selectConversation(cid,branch.id);toast(state.mode==='associate'?'已关联，后续任务继续原会话':state.mode==='rename'?'会话名称已保存':'工作会话已创建，尚未调用模型');
  }catch(e){$('session-dialog-error').textContent=e.message;$('session-dialog-error').hidden=false;}
  finally{$('session-dialog-confirm').disabled=false;}
});
$('related-dialog-close').addEventListener('click',()=>$('related-dialog').close());
$('related-dialog').addEventListener('close',()=>{relatedRequest++;$('related-messages').replaceChildren();clearCodePrefix('related');});
$('related-messages').addEventListener('click',async event=>{const button=event.target.closest('[data-code]');if(button){try{await navigator.clipboard.writeText(codeCopies.get(button.dataset.code)||'');toast('已复制');}catch(e){toast('复制不可用，请手动选择。');}}});
$('meeting-action-form').addEventListener('submit',confirmMeetingUnavailable);
$('meeting-action-dialog').addEventListener('close',()=>{if(!meetingActionState?.pending)meetingActionState=null;});
$('meeting-action-detail').addEventListener('click',event=>{const button=event.target.closest('[data-meeting-original][data-meeting-turn]');if(button){$('meeting-action-dialog').close();openMeetingTurn(button.dataset.meetingOriginal,button.dataset.meetingTurn);}});
$('meeting-focus-return').addEventListener('click',()=>setMeetingFocus());
$('meetings-older').addEventListener('click',loadOlderMeetings);
$('meetings-latest').addEventListener('click',latestMeetings);
$('turns-older').addEventListener('click',()=>pageConversation('older'));
$('turns-newer').addEventListener('click',()=>pageConversation('newer'));
$('turns-latest').addEventListener('click',()=>pageConversation('latest'));
$('detail-retry').addEventListener('click',()=>loadConversationPage(activeId,conversationPages.get(pageCacheKey(activeId))?.params||{},true));
$('live-memory').addEventListener('click',()=>{resourceExpanded=!resourceExpanded;accountExpanded=false;renderAccount();paintResources();if(resourceExpanded){readResources();$('resource-refresh').focus();}});
$('resource-close').addEventListener('click',()=>closeResources(true));
$('resource-refresh').addEventListener('click',()=>readResources(true));
window.addEventListener('resize',positionResources);

$('home-link').addEventListener('click',event=>{event.preventDefault();showHome();});
$('activity-nav').addEventListener('click',showActivity);
$('activity-overview').addEventListener('click',event=>{const fixed=event.target.closest('[data-bound-chat]');if(fixed&&!event.metaKey&&!event.ctrlKey&&!event.shiftKey&&!event.altKey){event.preventDefault();selectConversation(fixed.dataset.boundChat);return;}const button=event.target.closest('[data-session]');if(button)openActivitySession(button.dataset.session);});
$('history').addEventListener('click',event=>{const button=event.target.closest('[data-history-more],[data-history-open]');if(!button)return;const entry=historyEntries().find(e=>e.key===(button.dataset.historyMore||button.dataset.historyOpen));if(!entry)return;if(bulkMode){toggleHistorySelection(entry);return;}if(button.dataset.historyMore||entry.deleted){const box=button.getBoundingClientRect();openHistoryMenu(entry,box.right,box.bottom);}else if(entry.source==='panel')selectConversation(entry.id);else selectExternal(entry.id);});
$('history').addEventListener('contextmenu',event=>{const row=event.target.closest('[data-history-key]');if(!row)return;event.preventDefault();if(!bulkMode)openHistoryMenu(historyEntries().find(e=>e.key===row.dataset.historyKey),event.clientX,event.clientY);});
$('history').addEventListener('keydown',event=>{if(event.key!=='ContextMenu'&&!(event.shiftKey&&event.key==='F10'))return;const row=event.target.closest('[data-history-key]');if(row){event.preventDefault();const box=row.getBoundingClientRect();openHistoryMenu(historyEntries().find(e=>e.key===row.dataset.historyKey),box.right,box.bottom);}});
$('history-search').addEventListener('input',renderHistory);
$('history-filter').addEventListener('change',()=>{closeHistoryMenu();bulkMode=false;selectedHistory.clear();sessionStorage.setItem('agy-history-filter',$('history-filter').value);renderHistory();});
$('history-bulk-cancel').addEventListener('click',()=>{if(bulkBusy)return;bulkMode=false;selectedHistory.clear();renderHistory();});
$('history').addEventListener('change',event=>{const checkbox=event.target.closest('[data-history-select]');if(checkbox)toggleHistorySelection(historyEntries().find(e=>e.key===checkbox.dataset.historySelect));});
$('history-select-all').addEventListener('change',event=>{for(const entry of visibleHistory.filter(canBulkRemove)){if(event.target.checked)selectedHistory.add(entry.key);else selectedHistory.delete(entry.key);}renderHistory();});
$('history-bulk-remove').addEventListener('click',openBulkDialog);
$('bulk-dialog-cancel').addEventListener('click',()=>$('bulk-dialog').close());
$('bulk-dialog-form').addEventListener('submit',event=>{event.preventDefault();removeBulkTargets().catch(e=>{toast(e.message);$('bulk-dialog-error').textContent=e.message;$('bulk-dialog-error').hidden=false;});});
$('bulk-dialog').addEventListener('cancel',event=>{if(bulkBusy)event.preventDefault();});
$('history-menu').addEventListener('click',event=>{const button=event.target.closest('[data-history-action]');if(button&&!button.disabled&&historyMenuEntry)requestHistoryAction(historyMenuEntry,button.dataset.historyAction);});
document.addEventListener('pointerdown',event=>{if(resourceExpanded&&!event.target.closest('#resource-panel,#live-memory'))closeResources();if(!$('history-menu').hidden&&!event.target.closest('#history-menu,[data-history-more]'))closeHistoryMenu();if(openSelectId&&!event.target.closest('#select-menu,.select-picker'))closeSelectMenu();if(accountExpanded&&!event.target.closest('#account-panel,#account-toggle,#quota-ring')){accountExpanded=false;renderAccount();}});
$('select-menu').addEventListener('click',event=>{const option=event.target.closest('[data-select-value]'),picker=selectPickers.get(openSelectId);if(!option||option.disabled||!picker)return;const value=option.dataset.selectValue;closeSelectMenu(true);picker.select.value=value;picker.select.dispatchEvent(new Event('change',{bubbles:true}));syncSelectPickers();});
$('select-menu').addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeSelectMenu(true);return;}if(event.key==='Tab'){closeSelectMenu(true);return;}if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();event.stopPropagation();const options=[...$('select-menu').querySelectorAll('button:not(:disabled)')],index=options.indexOf(document.activeElement);options[event.key==='Home'?0:event.key==='End'?options.length-1:(index+(event.key==='ArrowDown'?1:options.length-1))%options.length]?.focus();}});
document.addEventListener('scroll',event=>{if(openSelectId&&!$('select-menu').contains(event.target))closeSelectMenu();},true);
$('history').addEventListener('scroll',closeHistoryMenu);
window.addEventListener('resize',closeHistoryMenu);
window.addEventListener('resize',()=>closeSelectMenu());
window.addEventListener('resize',positionAccountPanel);
window.addEventListener('storage',event=>{if(event.key===historyPinsKey||event.key===null){historyPins=loadHistoryPins();render();}if(event.key===uiSettingsKey||event.key===null){uiSettings=loadUISettings();applyUISettings();render();startPolling();}});
$('history-dialog-cancel').addEventListener('click',()=>$('history-dialog').close());
$('history-dialog-form').addEventListener('submit',async event=>{event.preventDefault();if($('history-dialog-confirm').disabled||!historyDialogEntry)return;$('history-dialog-confirm').disabled=true;$('history-dialog-error').hidden=true;try{await applyHistoryAction(historyDialogEntry,historyDialogAction,$('history-name').value.trim());$('history-dialog').close();}catch(e){$('history-dialog-error').textContent=e.message;$('history-dialog-error').hidden=false;}finally{$('history-dialog-confirm').disabled=false;}});
$('menu').addEventListener('click',()=>setSidebarOpen(!$('sidebar').classList.contains('open')));
setSidebarOpen(window.innerWidth>740);
$('prompt').addEventListener('input',()=>{$('prompt').style.height='auto';$('prompt').style.height=$('prompt').value?Math.min(220,$('prompt').scrollHeight)+'px':'38px';$('send').disabled=busy||submitting||modelSaving||!online||!snapshot.health.connected||!$('prompt').value.trim();queueDraftSave();});
async function submitComposer(event){
  event.preventDefault();const raw=$('prompt').value,text=raw.trim();if(!text||busy||submitting||modelSaving||reloadBusy||$('send').disabled)return;
  const model=snapshot.service?.features?.window_models?'':$('model').value,kind=$('task-kind').value,sourceKey=stashComposerDraft();let destination=composerConversation();const branch=selectedBranch(destination);
  submitting=true;render();
  try{if(!destination){destination=await api('/api/conversations',{});snapshot.conversations.push(destination);}
    const turn=await api(`/api/conversations/${destination.id}/turns`,{prompt:text,model,kind,...(branch?{branch}:{})});
    if(!destination.summary)destination.turns.push(turn);conversationPages.delete(pageCacheKey(destination.id));const follow=composerKey()===sourceKey;
    clearSubmittedDraft(sourceKey,raw);await refresh();if(follow){selectConversation(destination.id,turn.branch||branch);$('task-kind').value=kind;}startPolling();
  }catch(e){toast(e.message);}finally{submitting=false;render();}
}
$('composer').addEventListener('submit',submitComposer);
function composerKeydown(event){
  if(event.defaultPrevented||event.key!=='Enter'||event.shiftKey||event.altKey||event.isComposing||event.keyCode===229)return;
  event.preventDefault();
  if(!event.repeat)$('composer').requestSubmit();
}
$('prompt').addEventListener('keydown',composerKeydown);
$('model').addEventListener('change',()=>persistModelChoice());
$('model-family').addEventListener('change',()=>chooseModelFamily('model','model-family'));
$('model-speed').addEventListener('change',()=>chooseModelSpeed('model','model-speed'));
$('global-model-family').addEventListener('change',()=>{chooseModelFamily('global-model-select','global-model-family');saveGlobalModelChoice();});
$('global-model-speed').addEventListener('change',()=>{chooseModelSpeed('global-model-select','global-model-speed');saveGlobalModelChoice();});
$('model-quota-alternative').addEventListener('click',()=>{if(busy||submitting)return;$('model').value=$('model-quota-alternative').dataset.model;persistModelChoice();});
$('quota-ring').addEventListener('click',()=>{accountAnchor='composer';accountExpanded=!accountExpanded;renderAccount();});
$('task-kind').addEventListener('change',()=>{updateTaskMode();render();});
$('stop').addEventListener('click',async()=>{try{$('stop').disabled=true;await api(`/api/turns/${$('stop').dataset.turn}/cancel`,{});await refresh();}catch(e){toast(e.message);}});
$('connection').addEventListener('click',async()=>{try{await api('/api/reconnect',{});await refresh();}catch(e){toast(e.message);}});
$('account-refresh').addEventListener('click',async()=>{if(accountRefreshPending||!online)return;accountRefreshPending=true;renderAccount();try{await api('/api/account/refresh',{});await refresh();}catch(e){toast(e.message);}finally{accountRefreshPending=false;renderAccount();}});
$('account-toggle').addEventListener('click',()=>{closeSelectMenu();closeHistoryMenu();accountAnchor='header';accountExpanded=!accountExpanded;renderAccount();});
$('export').addEventListener('click',()=>{if(active())window.location.href=`/api/conversations/${activeId}/export`;});
document.addEventListener('keydown',event=>{if(['panel-action-dialog','meeting-action-dialog','fulltext-dialog','backup-dialog','software-update-dialog','config-import-dialog'].some(id=>$(id)?.open)||openSelectId||$('bulk-dialog').open||$('session-dialog').open||$('related-dialog').open||$('collaboration-audit-dialog').open||$('task-queue-dialog').open||$('settings-dialog').open)return;if($('image-preview').open){if(event.key==='Escape'){event.preventDefault();closeImagePreview();}return;}if($('history-dialog').open)return;if(resourceExpanded&&event.key==='Escape'){event.preventDefault();closeResources(true);return;}if(accountExpanded&&event.key==='Escape'){event.preventDefault();accountExpanded=false;renderAccount();$(accountAnchor==='composer'?'quota-ring':'account-toggle').focus();return;}if(!$('history-menu').hidden){if(event.key==='Escape'){event.preventDefault();closeHistoryMenu();return;}if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const buttons=[...$('history-menu').querySelectorAll('button:not([hidden]):not(:disabled)')],index=buttons.indexOf(document.activeElement);buttons[event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:buttons.length-1))%buttons.length]?.focus();return;}}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='k'){event.preventDefault();newConversation();}if(event.key==='Escape')setSidebarOpen(false);});
const templates={review:'请审查以下研究方案，按「关键问题、依据、修改建议」给出意见。区分事实与假设，不编造参考文献。\n\n[在这里粘贴研究方案]',writing:'请润色下面的论文段落，保留原意和所有数据，不补造结果与引用。先给修改稿，再说明主要修改。\n\n[在这里粘贴论文段落]',code:'请审查下面的统计代码，重点检查分析单位、缺失值处理、数据泄漏和结果解释。指出具体位置并给出修正建议。\n\n[在这里粘贴代码]',image:'请生成一张图片，按以下要求返回实际图片文件。\n\n主题：[填写图片内容]\n风格：[如简洁示意图、插画、写实]\n用途：[如论文配图、演示文稿]\n比例：[如 1:1、16:9]'};
document.querySelectorAll('[data-template]').forEach(button=>button.addEventListener('click',()=>{$('task-kind').value=button.dataset.template==='image'?'image':'text';updateTaskMode();$('prompt').value=templates[button.dataset.template];$('prompt').dispatchEvent(new Event('input'));render();$('prompt').focus();}));
$('messages').addEventListener('click',event=>{const action=event.target.closest('[data-meeting-action]');if(action){openMeetingAction(action.dataset.meetingId,action.dataset.meetingProvider,action.dataset.meetingStage,action.dataset.meetingAction);return;}const focus=event.target.closest('[data-meeting-focus]');if(focus){setMeetingFocus(focus.dataset.meetingFocus);return;}const summary=event.target.closest('summary'),details=summary?.parentElement;if(details?.matches('details[data-meeting-disclosure]'))rememberMeetingDisclosure(details.dataset.meetingDisclosure,details.dataset.meetingPart,!details.open);});
$('messages').addEventListener('toggle',event=>{const details=event.target;if(details.matches?.('details[data-progress-turn]')&&details.isConnected)rememberProgressExpansion(details.dataset.progressTurn,details.open);if(details.matches?.('details[data-dispatch-turn]')&&details.isConnected)rememberCodexPrompt(details.dataset.dispatchTurn,details.open);},true);
$('messages').addEventListener('click',async event=>{let button=event.target.closest('[data-code],[data-reply],[data-roundtable-reply],[data-meeting-copy]');if(!button)return;const meeting=button.dataset.meetingCopy?roundtableMeetings(active()).find(item=>item.id===button.dataset.meetingCopy):null;let text=button.dataset.code?codeCopies.get(button.dataset.code):meeting?meeting.slots.find(slot=>slot.provider===button.dataset.meetingProvider&&slot.stage===button.dataset.meetingStage)?.text:button.dataset.roundtableReply?roundtableMessages(active()).find(message=>message.id===button.dataset.roundtableReply)?.text:active()?.turns.find(t=>t.id===button.dataset.reply)?.response;try{await navigator.clipboard.writeText(text||'');toast('已复制');}catch(e){toast('复制不可用，请选择文本手动复制。');}});
$('messages').addEventListener('click',event=>{const button=event.target.closest('[data-meeting-original][data-meeting-turn]');if(button)openMeetingTurn(button.dataset.meetingOriginal,button.dataset.meetingTurn);});
$('messages').addEventListener('click',event=>{const button=event.target.closest('[data-image][data-image-turn]');if(button)showImagePreview(button.dataset.imageTurn,button.dataset.image);});
$('messages').addEventListener('error',event=>{if(event.target.tagName!=='IMG'||!event.target.closest('.image-card'))return;const button=event.target.closest('.image-open');event.target.hidden=true;button.disabled=true;button.querySelector('.image-open-hint').textContent='图片暂无法加载，请检查服务连接。';button.querySelector('.image-open-hint').className='image-load-failure';},true);
$('preview-close').addEventListener('click',closeImagePreview);
$('image-preview').addEventListener('click',event=>{if(event.target===$('image-preview'))closeImagePreview();});
$('image-preview').addEventListener('close',()=>{$('preview-image').removeAttribute('src');$('preview-image').alt='';$('preview-download').removeAttribute('href');$('preview-download').removeAttribute('download');});
function stopPolling(){pollEpoch++;memoryPollSync=null;for(const timer of pollTimers.values())clearTimeout(timer);pollTimers.clear();}
function pollingBusy(){return submitting||reloadBusy||snapshot.service?.active_tasks>0||snapshot.health?.checking||snapshot.health?.account?.state==='checking';}
function ensureRunClock(epoch){
  if(epoch!==pollEpoch||document.hidden||pollTimers.has('clock'))return;
  const tick=()=>{pollTimers.delete('clock');if(epoch!==pollEpoch||document.hidden)return;const turn=composerTurn(composerConversation());if(!turn)return;$('run-time').textContent=duration((Date.now()-new Date(turn.started_at||turn.created_at))/1000);pollTimers.set('clock',setTimeout(tick,1000));};tick();
}
function startPolling(){
  stopPolling();idlePolls=0;if(document.hidden||reloadBusy)return;const epoch=pollEpoch;
  const channel=(key,action,delay)=>{const cycle=async()=>{pollTimers.delete(key);if(epoch!==pollEpoch||document.hidden)return;try{await action();}finally{if(epoch===pollEpoch&&!document.hidden)pollTimers.set(key,setTimeout(cycle,typeof delay==='function'?delay():delay));}};cycle().catch(()=>{});};
  channel('state',async()=>{await refresh(false);idlePolls=pollingBusy()?0:Math.min(3,idlePolls+1);ensureRunClock(epoch);},()=>pollingBusy()?1600:Math.min(uiSettings.idleMs,4000*Math.max(1,idlePolls)));
  let memoryBusy=!!pollingBusy(),memoryInFlight=false,memoryAgain=false;
  const memoryCycle=async()=>{
    pollTimers.delete('resources');if(epoch!==pollEpoch||document.hidden)return;
    if(memoryInFlight){memoryAgain=true;return;}memoryInFlight=true;
    try{if(online&&snapshot.service?.features?.resources)await readMemorySummary();}
    finally{memoryInFlight=false;if(epoch===pollEpoch&&!document.hidden){const delay=memoryAgain?0:pollingBusy()?5000:20000;memoryAgain=false;pollTimers.set('resources',setTimeout(()=>memoryCycle().catch(()=>{}),delay));}}
  };
  memoryPollSync=()=>{const next=!!pollingBusy();if(next===memoryBusy||epoch!==pollEpoch||document.hidden)return;memoryBusy=next;clearTimeout(pollTimers.get('resources'));pollTimers.delete('resources');memoryCycle().catch(()=>{});};
  memoryCycle().catch(()=>{});
  channel('activity',refreshActivity,()=>['activity','external'].includes(viewMode)?2400:pollingBusy()?12000:30000);
}
document.addEventListener('visibilitychange',()=>{if(document.hidden)stashComposerDraft();startPolling();});
window.addEventListener('pagehide',()=>{stashComposerDraft();stopPolling();});
window.addEventListener('pageshow',event=>{if(event.persisted)startPolling();});
if(['all','panel','external','hidden','trash'].includes(sessionStorage.getItem('agy-history-filter')))$('history-filter').value=sessionStorage.getItem('agy-history-filter');
setupSelectPickers();
applyUISettings();
startPolling();
