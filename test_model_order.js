const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),between=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const store=new Map(),nodes=new Map();let renders=0;
const model=id=>({id,label:id.toUpperCase()}),models=['gemini-4-argon-high','gemini-3.8-flash-high','claude-sonnet-5-5-medium'].map(model);
const original=JSON.stringify(models),ctx={uiSettings:{font:16,idleMs:4000,modelOrder:[]},uiSettingsKey:'prefs',snapshot:{health:{models}},
  localStorage:{setItem:(k,v)=>store.set(k,v)},render:()=>renders++,toast(){},escape:s=>String(s).replaceAll('<','&lt;'),
  $:id=>{if(!nodes.has(id))nodes.set(id,{scrollTop:30});return nodes.get(id);}};
ctx.modelOrderSaving=false;vm.createContext(ctx);vm.runInContext(between('function cleanModelOrder','function loadUISettings'),ctx);vm.runInContext(between('function orderedModels','function renderModelSelection'),ctx);vm.runInContext(between('async function saveModelOrder','function installLightDismiss'),ctx);
ctx.moveModelOrder(models[1].id,'up');const saved=JSON.parse(store.get('prefs'));
assert.deepEqual(saved.modelOrder,['gemini-3.8-flash-high','gemini-4-argon-high','claude-sonnet-5-5-medium']);
assert.equal(saved.font,16);assert.equal(saved.idleMs,4000);assert.equal(renders,1);assert.equal(JSON.stringify(models),original);
assert.equal(ctx.orderedModels(models,saved.modelOrder)[0].id,models[1].id);
ctx.moveModelOrder(models[1].id,'up');assert.equal(renders,1);
ctx.saveModelOrder([]);assert.deepEqual(Array.from(ctx.orderedModels(models,ctx.uiSettings.modelOrder),m=>m.id),models.map(m=>m.id));
assert.deepEqual(Array.from(ctx.cleanModelOrder(['',models[0].id,models[0].id,'<invalid>',null])),[models[0].id]);
const node={value:models[1].id,dataset:{},innerHTML:''};Object.assign(ctx,{preferredModel:models[1].id,modelChoiceApplied:false,modelSaving:false,lastServerModelStamp:null,escape:String,$:()=>node});
ctx.snapshot.service={features:{global_model:true}};ctx.snapshot.preferred_model=models[1].id;ctx.snapshot.preferred_model_updated_at='saved';ctx.uiSettings.modelOrder=[models[2].id,models[1].id,models[0].id];
vm.runInContext(between('function renderModelSelection','async function persistModelChoice'),ctx);ctx.renderModelSelection({models},{});
assert.equal(node.value,models[1].id);assert.ok(node.innerHTML.startsWith('<option value="">智能选择（按额度）</option>'));assert.ok(node.innerHTML.indexOf(models[2].id)<node.innerHTML.indexOf(models[1].id));
console.log('Model-order checks passed: persisted order, stable input list, saved selection retained, smart option pinned, limits and boundaries, other preferences preserved.');
