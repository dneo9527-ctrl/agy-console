'use strict';
let panelAction=null,panelActionPending=false;
let fulltextState={sequence:0,pending:false,results:[],scope:null};
let backupPending=false,backupPreview=null;
let configTransferPending=false,configurationImport=null,configurationDownloadURL=null;

function taskActionMarkup(turn){
  let html='';
  const conv=conversationMeta(activeId),lastId=conv?.branches?.[turn.branch]?.last_turn_id;
  if(turn.id===lastId&&['failed','cancelled','interrupted'].includes(turn.status)&&snapshot.service?.features?.task_recovery)
    html+=`<button type="button" class="recover-task" data-recover-turn="${escape(turn.id)}">恢复任务</button>`;
  if(turn.action==='compact')html+='<span class="compact-proof">'+(isBusy(turn.status)?'正在整理上下文':turn.status!=='completed'?'整理未完成':turn.compaction_evidence?'检查点已核验':'指令已完成 · 检查点未核验')+'</span>';
  if(turn.action==='recovery')html+='<span class="compact-proof">手动恢复 · 原会话续接</span>';
  return html;
}
function renderPanelActions(){
  const scope=settingsInsightsScope(),conv=scope?conversationMeta(scope.cid):null,lane=conv?.branches?.[scope?.branch];
  const waiting=lane?.busy_count>0||(conv?.turns||[]).some(t=>t.branch===scope?.branch&&isBusy(t.status));
  $('compact-open').disabled=!online||!snapshot.service?.features?.context_compaction||!lane?.agy_id||waiting||panelActionPending;
  $('compact-open').dataset.tooltip=waiting?'请等待本工作会话的任务结束':!lane?.agy_id?'进入已有工作会话后可用':'手动整理当前工作会话，会使用模型额度';
  $('backup-open').disabled=!online||!snapshot.service?.features?.panel_backups;
  renderConfigurationControls();
}
function openPanelAction(action){
  if(panelActionPending)return;
  panelAction=action;$('panel-action-error').hidden=true;$('panel-action-ack').checked=false;
  $('recovery-detail').hidden=action.mode!=='recover';$('recovery-options').hidden=action.mode!=='recover';
  $('panel-action-ack-label').hidden=action.mode==='account';
  $('panel-action-ack').required=action.mode!=='account';
  if(action.mode==='recover'){
    const t=action.turn;
    $('panel-action-title').textContent='恢复任务';
    $('panel-action-note').textContent='继续「'+action.title+'」。上一轮可能已经执行部分操作，请先核对结果；模型只用于本次恢复。';
    $('recovery-previous').textContent=(t.error||texts[t.status]||t.status)+'\n\n'+(t.response||'尚无回复').slice(0,1800);
    $('recovery-strategy').value='continue';
    $('recovery-model').innerHTML='<option value="">沿用本窗口模型设置</option>'+orderedModels(snapshot.health?.models||[],uiSettings.modelOrder).map(m=>'<option value="'+escape(m.id)+'">'+escape(m.label)+'</option>').join('');
    $('panel-action-ack-text').textContent='已核对已有结果，确认恢复，避免重复操作';
    $('panel-action-submit').textContent='提交恢复';
  }else if(action.mode==='compact'){
    $('panel-action-title').textContent='整理上下文';
    $('panel-action-note').textContent='仅整理「'+action.title+'」的 Agy 上下文，保留目标、约束与关键进度。面板历史继续保留；压缩比例由 CLI 决定。';
    $('panel-action-ack-text').textContent='确认整理当前工作会话（使用模型额度）';
    $('panel-action-submit').textContent='开始整理';
  }else{
    $('panel-action-title').textContent='切换 CLI 账号';
    $('panel-action-note').textContent='打开 CC Switch 后自行选择账号。官方登录由本机所有 CLI 窗口共享；切完回此处刷新账号和额度，旧会话可能受账号权限影响。';
    $('panel-action-submit').textContent='打开 CC Switch';
  }
  $('settings-dialog').close();accountExpanded=false;renderAccount();closeSelectMenu();hideQuickTooltip();
  $('panel-action-submit').disabled=false;$('panel-action-dialog').showModal();
}
async function recoverTask(tid){
  const cid=activeId,conv=conversationMeta(cid);
  if(!conv||!online)return;
  try{
    const result=await api('/api/conversations/'+cid+'/turns/'+tid);
    if(activeId!==cid||viewMode!=='panel')return;
    if(result.id!==cid||result.turn?.id!==tid)throw new Error('返回的任务不匹配，请刷新后重试。');
    if(!['failed','cancelled','interrupted'].includes(result.turn?.status))throw new Error('本轮已结束或状态已变化，请刷新。');
    openPanelAction({mode:'recover',cid,tid,branch:result.turn.branch,title:conv.branches?.[result.turn.branch]?.title||conv.title,turn:result.turn});
  }catch(error){toast(error.message);}
}
async function submitPanelAction(event){
  event.preventDefault();if(!panelAction||panelActionPending)return;
  const action=panelAction,acknowledge=$('panel-action-ack').checked;
  if(action.mode!=='account'&&!acknowledge)return;
  const body=action.mode==='recover'?{strategy:$('recovery-strategy').value,model:$('recovery-model').value,acknowledge}:
    action.mode==='compact'?{branch:action.branch,acknowledge}:{};
  const path=action.mode==='account'?'/api/account/switch':'/api/conversations/'+action.cid+(action.mode==='recover'?'/turns/'+action.tid+'/recover':'/compact');
  panelActionPending=true;$('panel-action-submit').disabled=true;$('panel-action-error').hidden=true;
  try{
    await api(path,body);$('panel-action-dialog').close();
    if(action.mode==='account')toast('已打开 CC Switch，切换完成后刷新账号');
    else{
      conversationPages.delete(pageCacheKey(action.cid,{branch:action.branch}));await refresh();
      selectConversation(action.cid,action.branch);toast(action.mode==='compact'?'整理任务已提交，结果会在原会话显示':'已提交到原工作会话');
    }
  }catch(error){$('panel-action-error').textContent=error.message;$('panel-action-error').hidden=false;}
  finally{panelActionPending=false;$('panel-action-submit').disabled=false;renderSettings();}
}

function openFulltext(){
  const conv=viewMode==='panel'?conversationMeta(activeId):null;
  fulltextState={sequence:fulltextState.sequence+1,pending:false,results:[],scope:conv?.id||null};
  $('fulltext-scope').value=conv?'current':'all';$('fulltext-scope').options[0].disabled=!conv;
  $('fulltext-results').replaceChildren();$('fulltext-status').textContent='仅搜索面板已有记录，不调用模型。';
  $('fulltext-query').value=$('history-search').value.trim();$('fulltext-submit').disabled=false;
  setSidebarOpen(false);closeSelectMenu();$('fulltext-dialog').showModal();$('fulltext-query').focus();
}
function searchSnippet(text,query){
  const pattern=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'ig');let html='',last=0;
  for(const match of text.matchAll(pattern)){html+=escape(text.slice(last,match.index))+'<mark>'+escape(match[0])+'</mark>';last=match.index+match[0].length;}
  return html+escape(text.slice(last));
}
async function searchFulltext(event){
  event.preventDefault();if(fulltextState.pending)return;
  const query=$('fulltext-query').value.trim();if(!query)return;
  const sequence=++fulltextState.sequence,scope=$('fulltext-scope').value==='current'?fulltextState.scope:null;
  fulltextState.pending=true;$('fulltext-submit').disabled=true;$('fulltext-status').textContent='搜索中…';
  try{
    const params=new URLSearchParams({q:query,limit:'30',...(scope?{conversation:scope}:{})});
    const value=await api('/api/search?'+params);
    if(sequence!==fulltextState.sequence||!$('fulltext-dialog').open)return;
    fulltextState.results=value.results||[];
    $('fulltext-status').textContent=fulltextState.results.length+' 条匹配'+(value.truncated?' · 已达到扫描或结果上限，请缩小范围':'');
    $('fulltext-results').innerHTML=fulltextState.results.map((r,index)=>'<button type="button" class="fulltext-result" data-search-result="'+index+'"><strong>'+escape(r.title)+'<small>'+escape(r.session_title)+' · '+(r.role==='user'?'任务':'回复')+'</small></strong><span>'+searchSnippet(String(r.snippet),query)+'</span></button>').join('')||'<p class="action-note">未找到匹配记录。</p>';
  }catch(error){if(sequence===fulltextState.sequence)$('fulltext-status').textContent=error.message;}
  finally{if(sequence===fulltextState.sequence){fulltextState.pending=false;$('fulltext-submit').disabled=false;}}
}
async function jumpToSearchResult(index){
  const result=fulltextState.results[index];if(!result)return;
  $('fulltext-dialog').close();selectConversation(result.conversation_id,result.branch);
  await loadConversationPage(result.conversation_id,{branch:result.branch,around:result.turn_id,matchRole:result.role},true);
}

function backupPreferences(){return {settings:{...uiSettings},pins:[...historyPins]};}
function backupCounts(counts){return `${Number(counts.conversations)||0} 个聊天 · ${Number(counts.turns)||0} 轮 · ${Number(counts.images)||0} 张图片 · ${Number(counts.bindings)||0} 个固定绑定`;}
function backupLink(result,label){return result?.url&&/^\/api\/backups\/[a-f0-9]{32}\/download$/.test(result.url)?'<a class="backup-download" href="'+result.url+'" download>'+escape(label)+'</a>':'';}
function backupError(message){$('backup-error').textContent=message||'';$('backup-error').hidden=!message;}
function paintBackupBusy(){
  $('backup-export').disabled=backupPending;$('backup-file').disabled=backupPending;
  $('backup-restore').disabled=backupPending||!backupPreview||!$('backup-ack').checked;
}
function openBackups(){
  $('settings-dialog').close();$('backup-dialog').showModal();backupError('');paintBackupBusy();
}
async function exportBackup(){
  if(backupPending)return;backupPending=true;paintBackupBusy();backupError('');$('backup-status').textContent='正在生成备份…';
  try{
    const result=await api('/api/backups/export',{ui:backupPreferences()});
    $('backup-download').innerHTML=backupLink(result,'下载备份 ZIP · '+(result.bytes/1024/1024).toFixed(1)+' MB');
    $('backup-status').textContent=backupCounts(result.counts)+' · 请下载保存';
  }catch(error){backupError(error.message);$('backup-status').textContent='未生成备份，原数据保留。';}
  finally{backupPending=false;paintBackupBusy();}
}
async function previewBackup(){
  const file=$('backup-file').files?.[0];if(!file||backupPending)return;
  backupPreview=null;$('backup-preview').hidden=true;$('backup-ack').checked=false;backupError('');
  if(file.size>64*1024*1024){backupError('请选择64 MB以内的面板ZIP备份。');paintBackupBusy();return;}
  backupPending=true;paintBackupBusy();$('backup-status').textContent='上传并校验备份…';
  try{
    const response=await fetch('/api/backups/preview',{method:'POST',headers:{'Content-Type':'application/zip','X-Console-Token':token},body:file});
    const value=await response.json();if(!response.ok)throw new Error(value.error||'备份校验失败');
    backupPreview=value;$('backup-counts').textContent=backupCounts(value.counts)+' · '+(accountCheckedLabel(value.created_at)||'时间未知');
    $('backup-preview').hidden=false;$('backup-status').textContent='校验通过，确认后才会替换面板数据。';
  }catch(error){backupError(error.message);$('backup-status').textContent='尚未修改面板数据。';}
  finally{backupPending=false;paintBackupBusy();}
}
function restoreBackupPreferences(value){
  if(!value?.settings)return;
  const settings=value.settings;
  uiSettings={font:[14,16,18].includes(settings.font)?settings.font:14,idleMs:[4000,12000].includes(settings.idleMs)?settings.idleMs:12000,modelOrder:cleanModelOrder(settings.modelOrder)};
  historyPins=new Set((value.pins||[]).filter(x=>/^(?:panel:[a-f0-9]{32}|external:[a-f0-9-]{36})$/.test(x)));
  try{localStorage.setItem(uiSettingsKey,JSON.stringify(uiSettings));localStorage.setItem(historyPinsKey,JSON.stringify([...historyPins]));}
  catch(error){toast('数据已恢复，但浏览器未能保存部分显示设置。');}
  historyRenderKey='';applyUISettings();
}
async function restoreBackup(){
  if(backupPending||!backupPreview||!$('backup-ack').checked)return;
  const preview=backupPreview,restoreUI=$('backup-restore-ui').checked;
  backupPending=true;paintBackupBusy();backupError('');stopPolling();
  $('backup-status').textContent='保存当前数据并恢复备份…';
  try{
    if(stateRefreshPromise)await stateRefreshPromise;
    stashComposerDraft();
    const result=await api('/api/backups/restore',{id:preview.id,acknowledge:true,ui:backupPreferences()});
    for(const key of conversationIntents.keys())conversationIntents.set(key,conversationIntents.get(key)+1);
    conversationPages.clear();stateETag=null;lastRender='';externalTitleCache.clear();
    settingsInsights={key:null,sequence:settingsInsights.sequence+1,data:null,error:'',pending:false};
    if(restoreUI)restoreBackupPreferences(result.ui);
    backupPreview=null;$('backup-preview').hidden=true;$('backup-ack').checked=false;
    $('backup-download').innerHTML=backupLink(result.rollback,'下载恢复前的回滚备份');
    $('backup-status').textContent='面板已恢复。'+(result.missing_contexts?.length?' '+result.missing_contexts.length+' 个会话缺少本机官方上下文，已阻止直接续聊。':'')+(result.notice||'');
    await refresh();
  }catch(error){backupError(error.message);$('backup-status').textContent='未确认恢复完成，原数据与回滚文件保留。';}
  finally{backupPending=false;paintBackupBusy();startPolling();}
}

function renderConfigurationControls(){
  const disabled=configTransferPending||!online||!snapshot.service?.features?.configuration_transfer;
  $('config-export').disabled=disabled;$('config-import').disabled=disabled;$('config-file').disabled=disabled;
  $('config-import-confirm').disabled=configTransferPending||!configurationImport?.preview||!online;
  $('config-apply-window').disabled=configTransferPending||!configurationImport?.document?.settings?.window||!configurationImport?.target;
  $('config-import-cancel').disabled=configTransferPending;
}
function configurationNote(text){$('config-transfer-note').textContent=text;$('config-transfer-note').hidden=!text;}
function configurationLink(url,label){
  const link=document.createElement('a');link.href=url;link.textContent=label;link.className='backup-download';link.download='agy-settings.json';$('config-download').replaceChildren(link);return link;
}
async function exportConfiguration(){
  if(configTransferPending)return;configTransferPending=true;renderConfigurationControls();configurationNote('正在导出配置…');
  try{
    const value=await api('/api/configuration/export',{ui:{font:uiSettings.font,idleMs:uiSettings.idleMs},conversation_id:settingsOwner()?.id||null});
    if(configurationDownloadURL)URL.revokeObjectURL(configurationDownloadURL);
    configurationDownloadURL=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    configurationLink(configurationDownloadURL,'下载配置 JSON').click();configurationNote('配置已导出，不含聊天和账号凭据。');
  }catch(error){configurationNote('导出失败：'+error.message);}
  finally{configTransferPending=false;renderConfigurationControls();}
}
async function previewConfigurationFile(){
  const file=$('config-file').files?.[0];if(!file||configTransferPending)return;
  $('config-file').value='';configurationImport=null;
  if(file.size>65536){configurationNote('请选择64 KB以内的配置 JSON；聊天备份请使用“备份与恢复”。');return;}
  configTransferPending=true;renderConfigurationControls();configurationNote('正在校验配置…');
  const target=settingsOwner();
  try{
    const value=JSON.parse(await file.text());
    configurationImport={document:value,target:target?{id:target.id,title:target.title}:null,preview:null};
    $('config-apply-window').checked=false;
    await loadConfigurationPreview();
    $('settings-dialog').close();$('config-import-dialog').showModal();configurationNote('请预览后确认导入。');
  }catch(error){configurationImport=null;configurationNote('未导入：'+error.message);}
  finally{configTransferPending=false;renderConfigurationControls();}
}
async function loadConfigurationPreview(){
  if(!configurationImport)return;
  const current=configurationImport;current.preview=null;$('config-import-error').hidden=true;
  const preview=await api('/api/configuration/preview',{document:current.document,conversation_id:current.target?.id||null,apply_window:$('config-apply-window').checked});
  if(configurationImport!==current)return;
  current.preview=preview;current.document=preview.document;
  const settings=preview.document.settings;
  $('config-import-summary').textContent='全局模型：'+(settings.global.preferred_model||'智能选择')+'\n界面：'+settings.ui.font+'px · '+settings.ui.idleMs/1000+'秒同步\n更新源：'+(settings.updates.repository||'未配置')+'\n自动下载更新：'+(settings.updates.automatic?'开启':'关闭');
  $('config-import-window-label').textContent=!settings.window?'此文件没有窗口配置':current.target?'应用窗口配置到“'+current.target.title+'”':'进入聊天后可导入窗口模型、候补和开关';
}
async function changeConfigurationScope(){
  if(configTransferPending)return;configTransferPending=true;renderConfigurationControls();
  try{await loadConfigurationPreview();}catch(error){$('config-import-error').textContent=error.message;$('config-import-error').hidden=false;}
  finally{configTransferPending=false;renderConfigurationControls();}
}
async function importConfiguration(){
  if(configTransferPending||!configurationImport?.preview)return;
  const current=configurationImport,preview=current.preview;let applied=false;configTransferPending=true;renderConfigurationControls();$('config-import-error').hidden=true;
  try{
    const result=await api('/api/configuration/import',{document:current.document,conversation_id:current.target?.id||null,apply_window:preview.apply_window,revision:preview.revision,acknowledge:true,ui:{font:uiSettings.font,idleMs:uiSettings.idleMs}});
    applied=true;uiSettings={...uiSettings,...result.ui,modelOrder:current.document.settings.global.model_order};
    let persisted=true;try{localStorage.setItem(uiSettingsKey,JSON.stringify(uiSettings));}catch(error){persisted=false;}
    applyUISettings();conversationPages.clear();stateETag=null;
    configurationImport=null;$('config-import-dialog').close();$('settings-dialog').showModal();
    configurationNote(persisted?'配置已导入，原配置已另存，可下载恢复。':'后台配置已导入；浏览器无法保存显示设置，本页已应用。');
    if(/^\/api\/configuration\/backups\/[a-f0-9]{32}$/.test(result.rollback_url||''))configurationLink(result.rollback_url,'下载导入前配置');
    await refresh();startPolling();
  }catch(error){if(applied)configurationNote('配置已导入，页面状态暂未同步，请稍后刷新。');else{current.preview=null;$('config-import-error').textContent='未完成导入：'+error.message+' 请关闭后重新选择文件预览。';$('config-import-error').hidden=false;}}
  finally{configTransferPending=false;renderConfigurationControls();}
}

$('panel-action-form').addEventListener('submit',submitPanelAction);
$('messages').addEventListener('click',event=>{const button=event.target.closest('[data-recover-turn]');if(button)recoverTask(button.dataset.recoverTurn);});
$('compact-open').addEventListener('click',()=>{const scope=settingsInsightsScope();if(scope&&!$('compact-open').disabled)openPanelAction({mode:'compact',...scope});});
$('account-switch').addEventListener('click',()=>openPanelAction({mode:'account'}));
$('fulltext-open').addEventListener('click',openFulltext);
$('fulltext-form').addEventListener('submit',searchFulltext);
$('fulltext-results').addEventListener('click',event=>{const button=event.target.closest('[data-search-result]');if(button)jumpToSearchResult(Number(button.dataset.searchResult));});
$('fulltext-dialog').addEventListener('close',()=>{fulltextState.sequence++;fulltextState.pending=false;});
$('backup-open').addEventListener('click',openBackups);
$('backup-export').addEventListener('click',exportBackup);
$('backup-file').addEventListener('change',previewBackup);
$('backup-ack').addEventListener('change',paintBackupBusy);
$('backup-restore').addEventListener('click',restoreBackup);
$('config-export').addEventListener('click',exportConfiguration);
$('config-import').addEventListener('click',()=>$('config-file').click());
$('config-file').addEventListener('change',previewConfigurationFile);
$('config-apply-window').addEventListener('change',changeConfigurationScope);
$('config-import-confirm').addEventListener('click',importConfiguration);
$('config-import-dialog').addEventListener('cancel',event=>{if(configTransferPending)event.preventDefault();});
$('config-import-dialog').addEventListener('close',()=>{if(!configTransferPending&&configurationImport){configurationImport=null;configurationNote('已取消导入，配置未更改。');renderConfigurationControls();}});
for(const id of ['panel-action-dialog','fulltext-dialog','backup-dialog','config-import-dialog'])installLightDismiss($(id));
document.querySelectorAll('[data-close-dialog]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.closeDialog).close()));
