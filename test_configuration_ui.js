const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/panel_actions.js','utf8'),nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},hidden:false,checked:false,files:[],children:[],showModal(){this.open=true;},close(){this.open=false;},replaceChildren(...children){this.children=children;},click(){this.clicked=true;}});return nodes.get(id);};
const doc={format:'agy-console-settings',schema:1,app_version:'0.1.0-preview.3',settings:{global:{preferred_model:'flash',model_order:['flash','argon']},window:{model_preferences:{text:'flash'}},updates:{repository:'',automatic:false},ui:{font:18,idleMs:4000}}};
let owner={id:'chat-a',title:'当前聊天'},calls=[],toasts=[],stores=[];
const ctx={$:node,configurationImport:null,configTransferPending:false,configurationDownloadURL:null,online:true,uiSettings:{font:14,idleMs:12000,modelOrder:[]},uiSettingsKey:'settings',
  snapshot:{service:{features:{configuration_transfer:true}}},settingsOwner:()=>owner,conversationPages:new Map([['cached',{}]]),stateETag:'old',localStorage:{setItem:(k,v)=>stores.push({k,v})},
  document:{createElement:()=>node('link')},Blob,URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},applyUISettings(){},refresh:async()=>{},startPolling(){},
  api:async(path,body)=>{calls.push({path,body});if(path.endsWith('/export'))return doc;if(path.endsWith('/preview'))return {document:body.document,revision:'verified',apply_window:body.apply_window};return {imported:true,ui:doc.settings.ui,rollback_url:'/api/configuration/backups/'+'a'.repeat(32)};}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function renderConfigurationControls'),source.indexOf("$('panel-action-form').addEventListener")),ctx);
(async()=>{
  await ctx.exportConfiguration();assert.equal(calls[0].body.conversation_id,'chat-a');assert.equal(node('link').clicked,true);assert.match(node('config-transfer-note').textContent,/不含聊天/);
  node('config-file').files=[{size:100,text:async()=>JSON.stringify(doc)}];await ctx.previewConfigurationFile();
  assert.equal(node('config-import-dialog').open,true);assert.equal(node('config-apply-window').checked,false,'window choices opt in');assert.equal(calls.at(-1).body.apply_window,false);
  assert.equal(node('config-import-confirm').disabled,false);assert.match(node('config-import-summary').textContent,/18px/);
  owner={id:'other-chat',title:'其他聊天'};node('config-apply-window').checked=true;await ctx.changeConfigurationScope();assert.equal(calls.at(-1).body.conversation_id,'chat-a','preview keeps captured target');
  await ctx.importConfiguration();const action=calls.find(c=>c.path.endsWith('/import'));assert.equal(action.body.conversation_id,'chat-a');assert.equal(action.body.revision,'verified');assert.equal(action.body.acknowledge,true);
  assert.equal(ctx.uiSettings.font,18);assert.equal(ctx.conversationPages.size,0);assert.equal(node('config-import-dialog').open,false);assert.match(node('link').href,/configuration\/backups/);assert.equal(stores.length,1);
  const before=calls.length;node('config-file').files=[{size:65537,text:async()=>{throw Error('oversized file not read');}}];await ctx.previewConfigurationFile();assert.equal(calls.length,before);
  node('config-file').files=[{size:1,text:async()=>'{invalid'}];await ctx.previewConfigurationFile();assert.equal(calls.length,before);assert.match(node('config-transfer-note').textContent,/未导入/);
  node('config-file').files=[{size:100,text:async()=>JSON.stringify(doc)}];await ctx.previewConfigurationFile();const prior=ctx.uiSettings;
  ctx.api=async()=>{throw Error('stale settings');};await ctx.importConfiguration();assert.equal(ctx.uiSettings,prior);assert.equal(node('config-import-confirm').disabled,true);assert.match(node('config-import-error').textContent,/stale settings/);
  console.log('Configuration UI passed: real download, explicit preview, opt-in pinned target, bounded file parsing, rollback link, success-only UI commit and stale import refusal.');
})().catch(e=>{console.error(e);process.exit(1);});
