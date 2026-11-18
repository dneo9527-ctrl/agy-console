const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const info=path=>({schema_version:1,policy_path:'/tmp/policy.json',installation_root:'/local/console',launcher:{path}});
(async()=>{
  const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{dataset:{},value:'',setAttribute(k,v){this[k]=v;}});return nodes.get(id);};
  const a={id:'a',codex_thread_id:'thread-a',auto_start_console:false,auto_open_activity:false},b={id:'b',codex_thread_id:'thread-b',auto_start_console:false};
  const chats=new Map([['a',a],['b',b]]),calls=[],notices=[];let resolvePost;
  const ctx={$:node,document:{activeElement:null},online:true,consoleStartupData:null,consoleStartupPending:false,consoleStartupSaving:false,consoleAutoStartPending:false,consoleLauncherDirty:false,consoleStartupExpanded:false,consoleStartupSequence:0,
    snapshot:{service:{features:{multi_ai_collaboration:true},console_startup:info('')}},renderSettings(){},refresh:async()=>{},conversationMeta:id=>chats.get(id),toast:t=>notices.push(t),
    api:(path,body)=>{calls.push({path,body});return new Promise(resolve=>resolvePost=resolve);}};
  vm.createContext(ctx);vm.runInContext(between('function startupMetadata','async function setAutoOpenPreference'),ctx);
  ctx.renderConsoleStartupSettings(a);assert.equal(node('settings-auto-start')['aria-checked'],'false');assert.equal(node('console-launcher-path').value,'');assert.equal(calls.length,0);
  node('console-launcher-path').value='/tmp/fixture/中文 Launcher.command';ctx.consoleLauncherDirty=true;ctx.renderConsoleStartupSettings(a);assert.equal(node('console-launcher-path').value,'/tmp/fixture/中文 Launcher.command','polling preserves path draft');
  const save=ctx.saveConsoleLauncher();assert.equal(calls[0].path,'/api/console-startup');assert.equal(calls[0].body.launcher_path,'/tmp/fixture/中文 Launcher.command');assert.equal(ctx.consoleStartupSaving,true);
  await ctx.saveConsoleLauncher();assert.equal(calls.length,1);resolvePost(info('/tmp/fixture/中文 Launcher.command'));await save;assert.equal(ctx.consoleLauncherDirty,false);assert.equal(ctx.consoleStartupData.launcher.path,'/tmp/fixture/中文 Launcher.command');assert.match(notices.at(-1),/不会立即执行/);
  const auto=ctx.setConsoleAutoStart(a,true);assert.equal(calls[1].body.codex_thread_id,'thread-a');assert.equal(calls[1].body.auto_start_console,true);resolvePost({codex_thread_id:'thread-a',auto_start_console:true});await auto;assert.equal(a.auto_start_console,true);assert.equal(b.auto_start_console,false);assert.equal(a.auto_open_activity,false,'automatic presentation unchanged');
  const mismatch=ctx.setConsoleAutoStart(a,false);resolvePost({codex_thread_id:'thread-b',auto_start_console:false});await mismatch;assert.equal(a.auto_start_console,true);assert.match(notices.at(-1),/不匹配/);
  ctx.refresh=async()=>{throw new Error('refresh offline');};const savedButUnsynced=ctx.setConsoleAutoStart(a,false);resolvePost({codex_thread_id:'thread-a',auto_start_console:false});await savedButUnsynced;
  assert.equal(a.auto_start_console,false);assert.match(notices.at(-1),/已保存.*暂未同步.*refresh offline/,'successful save must not be reported as unsaved when refresh fails');ctx.refresh=async()=>{};
  ctx.snapshot.service.features={console_startup:true,multi_ai_collaboration:false};
  const lateRead=ctx.loadConsoleStartup(),finishLateRead=resolvePost;
  node('console-launcher-path').value='/tmp/New.command';ctx.consoleLauncherDirty=true;const newerSave=ctx.saveConsoleLauncher();resolvePost(info('/tmp/New.command'));await newerSave;
  finishLateRead(info('/tmp/Old.command'));await lateRead;assert.equal(ctx.consoleStartupData.launcher.path,'/tmp/New.command','late GET must not roll back saved launcher');assert.equal(ctx.consoleStartupPending,false);
  node('console-launcher-path').value='';const clear=ctx.saveConsoleLauncher();resolvePost(info(''));await clear;assert.equal(ctx.consoleStartupData.launcher.path,'');
  const read=ctx.loadConsoleStartup();assert.equal(calls.at(-1).path,'/api/console-startup');assert.equal(calls.at(-1).body,undefined);resolvePost(info('C:\\Tools\\Start-Agy.cmd'));await read;ctx.renderConsoleStartupSettings(a);assert.equal(node('console-startup-summary').textContent,'Start-Agy.cmd');
  ctx.renderConsoleStartupSettings(null);assert.equal(node('settings-auto-start').disabled,true,'overview cannot modify chat startup');
  ctx.renderConsoleStartupSettings({...a,auto_start_console:undefined});assert.equal(node('settings-auto-start').disabled,true);assert.equal(node('settings-auto-start').textContent,'待核实');
  const countBeforeUnknown=calls.length;await ctx.setConsoleAutoStart({...a,auto_start_console:undefined},true);assert.equal(calls.length,countBeforeUnknown,'unknown chat policy cannot be silently enabled');
  ctx.api=async()=>{throw new Error('invalid launcher');};node('console-launcher-path').value='/bad';await ctx.saveConsoleLauncher();assert.match(node('console-startup-error').textContent,/未保存.*invalid launcher/);assert.equal(ctx.consoleStartupData.launcher.path,'C:\\Tools\\Start-Agy.cmd');
  assert.ok(calls.every(c=>!c.path.includes('/turns')&&!c.path.includes('/launch')),'settings only read/save metadata; no process or model launch');
  console.log('Console startup UI checks passed: saved file metadata, isolated auto-start, no execution, exact response identity, preserved path draft, stale-read exclusion, independent capability and honest post-save refresh failure.');
})().catch(e=>{console.error(e);process.exit(1)});
