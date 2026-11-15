const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
async function submitCase(mode){
  const input={value:' original draft ',style:{}},model={value:'flash'},kind={value:'text'},destination={id:'a',summary:true};let key='a:main',resolve,reject,selected=0,cleared=0;
  const drafts=new Map([[key,input.value]]),ctx={busy:false,submitting:false,modelSaving:mode==='model-saving',reloadBusy:false,draftPending:null,draftSaveTimer:null,clearTimeout,sessionDrafts:drafts,
    draftStore:{clearSent(){cleared++;}},snapshot:{conversations:[destination]},conversationPages:new Map(),$:id=>({prompt:input,model,'task-kind':kind,send:{disabled:false}}[id]),
    composerKey:()=>key,stashComposerDraft:()=>key,composerConversation:()=>destination,selectedBranch:()=> 'main',pageCacheKey:()=>key,render(){},refresh:async()=>{},startPolling(){},selectConversation(){selected++;},toast(){},
    api:(path,body)=>{assert.equal(body.prompt,'original draft');return new Promise((a,b)=>{resolve=a;reject=b;});}};
  vm.createContext(ctx);vm.runInContext(between('function clearSubmittedDraft','function rememberFixedOwner'),ctx);vm.runInContext(between('async function submitComposer',"$('composer').addEventListener('submit'"),ctx);
  const request=ctx.submitComposer({preventDefault(){}});await flush();
  if(mode==='model-saving'){await request;assert.equal(resolve,undefined,'cannot submit using the old model while a new selection is being saved');assert.equal(input.value,' original draft ');assert.equal(cleared,0);return;}
  if(mode==='new-input'){input.value='next draft';drafts.set(key,input.value);ctx.draftPending={key,text:input.value};}
  if(mode==='different-chat'){key='b:main';input.value='other chat draft';drafts.set(key,input.value);}
  if(mode==='fail')reject(new Error('network failed'));else resolve({id:'turn',branch:'main'});
  await request;
  if(mode==='plain'){assert.equal(input.value,'');assert.equal(drafts.has('a:main'),false);assert.equal(selected,1);}
  if(mode==='new-input'){assert.equal(input.value,'next draft');assert.equal(drafts.get(key),'next draft');assert.equal(ctx.draftPending.text,'next draft');}
  if(mode==='different-chat'){assert.equal(input.value,'other chat draft');assert.equal(selected,0);}
  if(mode==='fail'){assert.equal(input.value,' original draft ');assert.equal(drafts.get(key),input.value);assert.equal(cleared,0);}
  assert.equal(ctx.submitting,false);
}
async function reloadCase(mode){
  let posted=0,gets=0,reloads=0,saved=0,started=0;
  const ctx={reloadBusy:false,snapshot:{service:{can_reload:mode!=='busy',instance_id:'old'}},draftPersisted:true,
    $:()=>({value:'saved draft',close(){}}),stashComposerDraft(){saved++;},stopPolling(){},startPolling(){started++;},render(){},toast(){},
    api:async path=>{assert.equal(path,'/api/service/reload');posted++;if(mode==='refused')throw new Error('busy');},
    fetch:async path=>{assert.equal(path,'/api/service');gets++;return {ok:true,json:async()=>({instance_id:mode==='timeout'?'old':'new'})};},
    setTimeout:fn=>{fn();return 1;},location:{reload(){reloads++;}}};
  vm.createContext(ctx);vm.runInContext(between('async function loadUpdatedPage','function taskErrorHTML'),ctx);
  await ctx.reloadService();
  if(mode==='busy'){assert.equal(posted,0);assert.equal(saved,0);}
  else{assert.equal(posted,1);assert.equal(ctx.reloadBusy,false);assert.equal(started,1);}
  assert.equal(reloads,mode==='success'?1:0);assert.equal(gets,mode==='timeout'?30:mode==='success'?1:0);
}
(async()=>{for(const mode of ['plain','new-input','different-chat','fail','model-saving'])await submitCase(mode);for(const mode of ['busy','refused','timeout','success'])await reloadCase(mode);console.log('Workflow checks passed: accepted-send cleanup, preservation during pending requests and failures, model-save/send exclusion, chat isolation, idle-only single reload request, bounded reconnect and saved draft.');})().catch(e=>{console.error(e);process.exit(1);});
