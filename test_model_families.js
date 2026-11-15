const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
class Select{
  constructor(){this.options=[];this.dataset={};this.disabled=false;this.value='';this.html='';this.changes=[];}
  set innerHTML(html){this.html=html;this.options=[...html.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)].map(m=>({value:m[1],text:m[3],disabled:m[2].includes('disabled')}));}
  get innerHTML(){return this.html;}
  dispatchEvent(event){this.changes.push({type:event.type,value:this.value});}
}
const model=(id,label)=>({id,label}),models=[model('argon-high','Argon (High)'),model('hydrogen-xhigh','Hydrogen (XHigh)'),model('argon-low','Argon (Low)'),model('hydrogen-high','Hydrogen (High)'),model('orchestrator-pro','Orchestrator (Argon)'),model('oss-medium','OSS (Medium)')];
const nodes=Object.fromEntries(['model','model-family','model-speed','global-model-select','global-model-family','global-model-speed'].map(id=>[id,new Select()]));
for(const id of ['model','global-model-select'])nodes[id].innerHTML='<option value="__global__">沿用全局</option><option value="">智能</option>'+models.map(m=>`<option value="${m.id}">${m.label}</option>`).join('')+'<option value="unavailable" disabled>旧模型</option>';
const ctx={snapshot:{health:{models}},uiSettings:{modelOrder:[]},$:id=>nodes[id],escape:String,Event:class{constructor(type){this.type=type;}}};
ctx.modelOrderSaving=false;vm.createContext(ctx);vm.runInContext(between('function orderedModels','function renderModelSelection'),ctx);
const groups=ctx.modelFamilies(models);assert.equal(groups.length,4);assert.deepEqual(Array.from(groups.flatMap(g=>g.variants.map(m=>m.id))).sort(),models.map(m=>m.id).sort());
for(const m of models){nodes.model.value=m.id;ctx.syncComposerModelPickers();assert.equal(nodes.model.value,m.id);assert.equal(nodes['model-speed'].value,m.id);}
nodes.model.value='argon-high';ctx.syncComposerModelPickers();assert.equal(nodes['model-family'].value,'family:argon');
assert.deepEqual(Array.from(nodes['model-speed'].options,o=>o.value),['argon-low','argon-high']);
nodes['model-family'].value='family:hydrogen';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'hydrogen-high','same effort survives family change');
assert.equal(nodes.model.changes.at(-1).value,'hydrogen-high');
nodes['model-speed'].value='hydrogen-xhigh';ctx.chooseModelSpeed('model','model-speed');assert.equal(nodes.model.value,'hydrogen-xhigh');
nodes['model-family'].value='family:argon';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'argon-high','missing effort falls back to the first sorted family variant');
for(const id of ['','__global__']){nodes.model.value=id;ctx.syncComposerModelPickers();assert.equal(nodes['model-speed'].disabled,true);assert.equal(nodes['model-family'].value,id);assert.equal(nodes.model.value,id);}
nodes.model.value='orchestrator-pro';ctx.syncComposerModelPickers();assert.equal(nodes['model-speed'].disabled,true);
nodes.model.value='unavailable';ctx.syncComposerModelPickers();assert.equal(nodes['model-family'].options.find(o=>o.value==='unavailable').disabled,true);assert.equal(nodes.model.value,'unavailable');
nodes.model.disabled=true;ctx.syncComposerModelPickers();nodes['model-family'].value='family:hydrogen';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'unavailable');
nodes['global-model-select'].value='argon-high';ctx.splitModelSelection('global-model-select','global-model-family','global-model-speed');
nodes['global-model-family'].value='family:hydrogen';ctx.chooseModelFamily('global-model-select','global-model-family');assert.equal(nodes['global-model-select'].value,'hydrogen-high');assert.equal(nodes['global-model-speed'].value,'hydrogen-high');
nodes['global-model-speed'].value='hydrogen-xhigh';ctx.chooseModelSpeed('global-model-select','global-model-speed');assert.equal(nodes['global-model-select'].value,'hydrogen-xhigh');assert.equal(nodes['global-model-select'].changes.length,0,'projection itself does not post; global change handler saves');
ctx.cleanModelOrder=a=>a;ctx.render=()=>{};ctx.localStorage={setItem(){}};ctx.toast=()=>{};ctx.uiSettingsKey='settings';nodes['settings-model-order-list']={scrollTop:20};
vm.runInContext(between('async function saveModelOrder','function installLightDismiss'),ctx);ctx.moveModelOrder('hydrogen-high','up');
assert.deepEqual(Array.from(ctx.uiSettings.modelOrder).sort(),models.map(m=>m.id).sort());assert.deepEqual(Array.from(ctx.uiSettings.modelOrder).slice(0,2),['hydrogen-xhigh','hydrogen-high']);
assert.equal(nodes.model.value,'unavailable');
nodes.model.disabled=false;nodes.model.value='hydrogen-high';
nodes.model.options.find(o=>o.value==='argon-high').disabled=true;ctx.syncComposerModelPickers();
nodes['model-family'].value='family:argon';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'argon-low','skip disabled variants instead of leaving a stale visual choice');
ctx.syncComposerModelPickers();assert.equal(nodes['model-speed'].options.find(o=>o.value==='argon-high').disabled,true);
nodes['model-speed'].value='argon-high';ctx.chooseModelSpeed('model','model-speed');assert.equal(nodes.model.value,'argon-low');assert.equal(nodes['model-speed'].value,'argon-low');
nodes.model.options.find(o=>o.value==='argon-low').disabled=true;nodes.model.value='hydrogen-high';ctx.syncComposerModelPickers();
nodes['model-family'].value='family:argon';ctx.chooseModelFamily('model','model-family');assert.equal(nodes.model.value,'hydrogen-high');assert.equal(nodes['model-family'].value,'family:hydrogen');
console.log('Model family checks passed: every official slug, special defaults, exact effort, single tier, disabled choices, global projection, complete family sorting.');
