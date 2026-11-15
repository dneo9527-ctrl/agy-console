const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
class Select{
  constructor(){this.value='';this.dataset={};this.options=[];this.disabled=false;this.listeners={};this.open=false;}
  set innerHTML(html){this.html=html;this.options=[...html.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)].map(m=>({value:m[1],text:m[3],disabled:m[2].includes('disabled')}));}
  get innerHTML(){return this.html;}
  setAttribute(){} dispatchEvent(){} addEventListener(name,fn){this.listeners[name]=fn;}
  close(){this.open=false;this.listeners.close?.();} showModal(){this.open=true;} show(){this.open=true;} focus(){}
}
const nodes=Object.fromEntries(['model','model-family','model-speed','task-kind','global-model-select','global-model-family','global-model-speed','global-model-error','settings-model-label'].map(id=>[id,new Select()]));
const models=['argon-low','argon-high','flash-low','flash-high'].map(id=>({id,label:id.replace(/-(low|high)$/,(_,e)=>' ('+e[0].toUpperCase()+e.slice(1)+')')}));
const chats={a:{id:'a',model_preferences:{text:'argon-high'}},b:{id:'b',model_preferences:{}}};let current=chats.a;
const storage=new Map();let writes=0,resolve,reject,requests=0;
const ctx={snapshot:{service:{features:{window_models:true,global_model:true}},health:{models},preferred_model:'argon-high'},uiSettings:{modelOrder:[]},$:id=>nodes[id],escape:String,Event:class{},
  localStorage:{getItem:key=>storage.get(key),setItem:(key,value)=>{writes++;storage.set(key,value);}},
  composerConversation:()=>current,conversationMeta:id=>chats[id],conversationPages:new Map(),pageCacheKey:id=>id,modelSaving:false,online:true,preferredModel:'argon-high',lastServerModelStamp:null,
  render(){},toast(){},installLightDismiss(){},api:()=>{requests++;return new Promise((yes,no)=>{resolve=yes;reject=no;});}};
vm.createContext(ctx);vm.runInContext(between('function orderedModels','function renderModelQuota'),ctx);
vm.runInContext(between('function renderGlobalModelSettings','let updateActionPending'),ctx);vm.runInContext(between('async function saveGlobalModelChoice',"$('fallback-expand')"),ctx);
nodes['task-kind'].value='text';ctx.renderModelSelection(ctx.snapshot.health,current);ctx.syncComposerModelPickers();assert.equal(writes,0,'render never writes memory');
(async()=>{
  let save=ctx.persistModelChoice('flash-low');current=chats.b;nodes['task-kind'].value='image';
  resolve({id:'a',model_preferences:{text:'flash-low'}});await save;
  assert.equal(ctx.readModelEfforts()[0].scope,JSON.stringify(['a','text']),'save uses captured chat and kind');
  current=chats.a;nodes['task-kind'].value='text';save=ctx.persistModelChoice('flash-high');reject(Error('offline'));await save;
  assert.equal(ctx.readModelEfforts()[0].model,'flash-low','failed save cannot poison memory');
  for(const value of ['__global__','']){save=ctx.persistModelChoice(value);resolve({id:'a',model_preferences:value?{}:{text:''}});await save;}
  assert.equal(ctx.readModelEfforts().length,1,'inherit and smart do not replace tier history');
  ctx.renderModelSelection(ctx.snapshot.health,current);nodes.model.value='argon-high';nodes['model-family'].value='family:flash';ctx.chooseModelFamily('model','model-family');
  assert.equal(nodes.model.value,'flash-low','restores saved family tier');
  current=chats.b;nodes.model.value='argon-high';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'flash-high','other chat inherits no private tier');
  current=chats.a;nodes['task-kind'].value='image';nodes.model.value='argon-high';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'flash-high','image has independent tier memory');
  nodes['task-kind'].value='text';nodes.model.options.find(o=>o.value==='flash-low').disabled=true;nodes.model.value='argon-high';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'flash-high','unavailable remembered tier falls back safely');
  const beforeGlobal=writes,independent=JSON.stringify(chats);
  ctx.renderGlobalModelSettings();assert.equal(nodes['global-model-select'].value,'argon-high');
  nodes['global-model-family'].value='family:flash';ctx.chooseModelFamily('global-model-select','global-model-family');
  nodes['global-model-speed'].value='flash-low';ctx.chooseModelSpeed('global-model-select','global-model-speed');
  save=ctx.saveGlobalModelChoice();const pendingRequests=requests;await ctx.saveGlobalModelChoice();assert.equal(requests,pendingRequests,'duplicate change cannot post while saving');assert.equal(ctx.modelSaving,true);assert.equal(nodes['global-model-family'].disabled,true);
  ctx.renderGlobalModelSettings();assert.equal(nodes['global-model-select'].value,'flash-low','polling keeps the in-flight choice');
  reject(Error('offline'));await save;assert.equal(writes,beforeGlobal,'failed automatic save does not commit memory');
  assert.equal(nodes['global-model-select'].value,'argon-high','failed save rolls back to stored choice');assert.equal(nodes['global-model-error'].hidden,false);
  nodes['global-model-family'].value='family:flash';ctx.chooseModelFamily('global-model-select','global-model-family');
  nodes['global-model-speed'].value='flash-low';ctx.chooseModelSpeed('global-model-select','global-model-speed');
  save=ctx.saveGlobalModelChoice();resolve({preferred_model:'flash-low',preferred_model_updated_at:'now'});await save;
  assert.equal(ctx.readModelEfforts().find(r=>r.scope==='global'&&r.family==='flash').model,'flash-low');
  assert.equal(ctx.readModelEfforts().find(r=>r.scope===JSON.stringify(['a','text'])).model,'flash-low');
  assert.equal(nodes['global-model-select'].value,'flash-low');assert.equal(nodes['settings-model-label'].textContent,'自动保存');
  const beforeSmart=writes;nodes['global-model-family'].value='';ctx.chooseModelFamily('global-model-select','global-model-family');
  save=ctx.saveGlobalModelChoice();resolve({preferred_model:'',preferred_model_updated_at:'later'});await save;assert.equal(writes,beforeSmart,'saving smart does not rewrite family memory');
  nodes['global-model-family'].value='family:argon';ctx.chooseModelFamily('global-model-select','global-model-family');save=ctx.saveGlobalModelChoice();
  resolve({preferred_model:'wrong-model',preferred_model_updated_at:'invalid'});await save;assert.equal(ctx.snapshot.preferred_model,'','mismatched response cannot overwrite saved global choice');
  assert.equal(JSON.stringify(chats),independent,'global change leaves independent window choices untouched');
  const html=fs.readFileSync(__dirname+'/index.html','utf8');assert.ok(!html.includes('settings-model-open'));assert.ok(!html.includes('global-model-dialog'));assert.ok(html.includes('settings-global-controls'));
  for(let i=0;i<300;i++)ctx.rememberModelEfforts('chat-'+i,['flash-low']);assert.equal(ctx.readModelEfforts().length,256,'storage remains bounded');
  ctx.localStorage.getItem=()=>'{bad-json';assert.equal(ctx.readModelEfforts().length,0);
  ctx.localStorage.setItem=()=>{throw Error('blocked');};ctx.rememberModelEfforts('global',['flash-low']);
  console.log('Effort memory passed: successful saves only, chat/kind isolation, unavailable fallback, automatic global save, rollback/mismatched response, no modal, bounded and unavailable storage.');
})().catch(e=>{console.error(e);process.exit(1);});
