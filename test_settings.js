const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),stored=new Map(),nodes=new Map();let polls=0;
const fonts=[14,16,18].map(v=>({dataset:{fontSize:String(v)},setAttribute(k,x){this[k]=x;}})),delays=[4000,12000].map(v=>({dataset:{idleMs:String(v)},setAttribute(k,x){this[k]=x;}}));
const node=id=>{if(!nodes.has(id))nodes.set(id,{open:false,dataset:{},setAttribute(k,v){this[k]=v;}});return nodes.get(id);};
node('settings-dialog').open=true;node('model').selectedOptions=[{textContent:'Saved global model'}];node('model').disabled=false;
const chats=new Map([['a',{id:'a',title:'Current <chat>',codex_thread_id:'thread-a',gpt_collaboration:false,multi_ai_collaboration:{schema_version:1,codex_thread_id:'thread-a',enabled:false,providers:{agy:{enabled:true},muse:{enabled:false},claude:{enabled:false}}}}]]),toasts=[];
const ctx={localStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v)},$:node,
  document:{querySelector:()=>null,documentElement:{style:{setProperty:(k,v)=>stored.set('css:'+k,v)}},querySelectorAll:selector=>selector==='[data-font-size]'?fonts:delays},
  viewMode:'panel',activeId:'a',conversationMeta:id=>chats.get(id),fixedOwnerConversation:()=>chats.get('a'),online:true,collaborationPending:false,autoOpenPending:false,reloadBusy:false,modelSaving:false,
  multiAIExpanded:false,consoleStartupExpanded:false,consoleStartupPending:false,consoleStartupSaving:false,consoleAutoStartPending:false,consoleLauncherDirty:false,consoleStartupData:null,requestedCodexThread:null,
  snapshot:{preferred_model:'global',health:{models:[{id:'global',label:'Saved global model'}]},service:{features:{multi_ai_collaboration:true,gpt_collaboration:true,foreground_reload:true,panel_auto_open:true},can_reload:true}},startPolling(){polls++;},toast:t=>toasts.push(t)};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function multiAIState'),source.indexOf('async function setAutoOpenPreference')),ctx);vm.runInContext(source.slice(source.indexOf('const uiSettingsKey'),source.indexOf('function hideQuickTooltip')),ctx);
ctx.renderSettingsInsights=()=>{};ctx.renderSoftwareUpdates=()=>{};ctx.renderGlobalModelSettings=()=>{node('settings-model-label').textContent='自动保存';};
ctx.renderSettings();assert.equal(node('settings-collaboration').dataset.conversation,'a');assert.equal(node('settings-collaboration')['aria-checked'],'false');assert.equal(node('settings-model-label').textContent,'自动保存');
assert.equal(node('settings-auto-open')['aria-checked'],'true');assert.equal(node('settings-auto-open').dataset.conversation,'a');assert.equal(node('settings-auto-open').disabled,false);
chats.get('a').auto_open_activity=false;ctx.renderSettings();assert.equal(node('settings-auto-open')['aria-checked'],'false');assert.equal(node('settings-auto-open').textContent,'关');
ctx.snapshot.service.features.panel_auto_open=false;ctx.renderSettings();assert.equal(node('settings-auto-open').disabled,true);assert.equal(node('settings-auto-open').textContent,'待更新');ctx.snapshot.service.features.panel_auto_open=true;
ctx.saveUISetting('font',16);assert.equal(stored.get('css:--chat-font-size'),'16px');assert.equal(fonts[1]['aria-pressed'],'true');
ctx.saveUISetting('idleMs',4000);assert.equal(polls,1);assert.equal(delays[0]['aria-pressed'],'true');assert.equal(JSON.parse(stored.get('agy-ui-settings-v1')).idleMs,4000);
assert.equal(vm.runInContext('loadUISettings().font',ctx),16);
stored.set('agy-ui-settings-v1',JSON.stringify({font:16,idleMs:4000,theme:'night',modelOrder:['saved-model']}));
assert.equal(vm.runInContext('loadUISettings().theme',ctx),'light');assert.equal(JSON.parse(stored.get('agy-ui-settings-v1')).theme,'light','old deep-night choice migrates durably to white');assert.equal(JSON.parse(stored.get('agy-ui-settings-v1')).font,16);assert.deepEqual(JSON.parse(stored.get('agy-ui-settings-v1')).modelOrder,['saved-model']);
for(const theme of ['obsidian','graphite','ocean','warm','light']){stored.set('agy-ui-settings-v1',JSON.stringify({theme}));assert.equal(vm.runInContext('loadUISettings().theme',ctx),theme,'existing other theme choice survives');}
ctx.saveUISetting('theme','light');assert.equal(stored.get('css:--bg'),'#ffffff');assert.equal(stored.get('css:--text'),'#1f2937');

stored.set('agy-ui-settings-v1','{"font":99,"idleMs":1}');assert.equal(vm.runInContext('loadUISettings().font',ctx),14);assert.equal(vm.runInContext('loadUISettings().idleMs',ctx),12000);
ctx.activeId='missing';ctx.renderSettings();assert.equal(node('settings-collaboration').disabled,true);
ctx.viewMode='home';ctx.requestedCodexThread=null;ctx.renderSettings();assert.equal(node('settings-collaboration').disabled,true,'overview cannot inherit previous stored binding');ctx.viewMode='panel';
ctx.activeId='a';ctx.snapshot.service.can_reload=false;ctx.renderSettings();assert.ok(node('settings-reload-state').textContent.includes('等待'));
ctx.localStorage.setItem=()=>{throw new Error('full');};ctx.saveUISetting('font',18);assert.equal(stored.get('css:--chat-font-size'),'18px');assert.equal(toasts.length,1);
let notices=0;ctx.renderSoftwareUpdates=()=>notices++;node('settings-dialog').open=false;ctx.renderSettings();assert.equal(notices,1,'sidebar update notice remains fresh with Settings closed');
console.log('Settings checks passed: current chat scope, persisted and clamped preferences, matching actual model, local-only font/poll changes, safe storage failure.');
