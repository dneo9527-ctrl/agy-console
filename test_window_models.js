const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
function node(){return {value:'',dataset:{},innerHTML:'',setAttribute(){}};}
const nodes={'task-kind':{value:'text'},model:node()},a={id:'a',model_preferences:{text:'flash',image:'argon'}},b={id:'b',model_preferences:{text:''}};
const models=[{id:'argon',label:'Argon'},{id:'flash',label:'Flash'}];let current=a,calls=[],resolve;
const ctx={snapshot:{service:{features:{window_models:true}},preferred_model:'argon',health:{models}},uiSettings:{modelOrder:[]},$:id=>nodes[id],
  composerConversation:()=>current,conversationMeta:id=>id==='a'?a:b,conversationPages:new Map(),pageCacheKey:id=>id,modelSaving:false,
  preferredModel:'argon',modelChoiceApplied:false,lastServerModelStamp:null,escape:String,render(){},toast(){},api:(path,body)=>{calls.push({path,body});return new Promise(r=>resolve=r);}};
vm.createContext(ctx);vm.runInContext(between('function orderedModels','function renderModelQuota'),ctx);
ctx.renderModelSelection({models},a);assert.equal(nodes.model.value,'flash');
nodes['task-kind'].value='image';ctx.renderModelSelection({models},a);assert.equal(nodes.model.value,'argon');
current=b;ctx.renderModelSelection({models},b);assert.equal(nodes.model.value,'__global__');
nodes['task-kind'].value='text';ctx.renderModelSelection({models},b);assert.equal(nodes.model.value,'');
ctx.snapshot.preferred_model='flash';current=a;ctx.renderModelSelection({models},a);assert.equal(nodes.model.value,'flash');
nodes['task-kind'].value='image';ctx.renderModelSelection({models},a);assert.equal(nodes.model.value,'argon','global change cannot overwrite image choice');
(async()=>{
  nodes.model.value='flash';const pending=ctx.persistModelChoice();
  assert.equal(calls[0].path,'/api/conversations/a/model');assert.equal(calls[0].body.kind,'image');
  current=b;nodes['task-kind'].value='text';
  resolve({id:'a',model_preferences:{text:'flash',image:'flash'}});await pending;
  assert.equal(a.model_preferences.image,'flash');assert.equal(b.model_preferences.text,'');assert.equal(ctx.snapshot.preferred_model,'flash');
  current=a;nodes.model.value='__global__';const inherit=ctx.persistModelChoice();assert.equal(calls[1].body.model,null);resolve({id:'a',model_preferences:{image:'flash'}});await inherit;
  assert.equal(a.model_preferences.text,undefined);
  console.log('Window model checks passed: independent text/image/chat choices, scoped saves, same global fallback, async reply never writes another window, inherit reset.');
})().catch(e=>{console.error(e);process.exit(1);});
