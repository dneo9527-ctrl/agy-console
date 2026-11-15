// Compare the UI's displayed candidate order/tiers with the real Python policy.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),cp=require('node:child_process');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const ids=['gemini-4-argon-medium','gemini-4-argon-high','gemini-3.8-flash-low','gemini-3.8-flash-high','claude-sonnet-5-5-low','claude-sonnet-5-5-medium'];
const py='import json,sys;from model_policy import effective_order,candidate_models;s=json.load(sys.stdin);print(json.dumps({"effective":effective_order(s),"default":candidate_models(s),"custom":candidate_models(s,allowed=s["allowed"])}))';
for(const order of [[],['gemini-3.8-flash-low','gemini-4-argon-medium','claude-sonnet-5-5-low']]){
 const snapshot={health:{models:ids.map(id=>({id,label:id}))},model_order:order,allowed:['claude-sonnet-5-5-low','gemini-3.8-flash-low']};
 const run=cp.spawnSync('python3',['-c',py],{cwd:__dirname,input:JSON.stringify(snapshot),encoding:'utf8'});assert.equal(run.status,0,run.stderr);const expected=JSON.parse(run.stdout);snapshot.effective_model_order=expected.effective;
 const ctx={snapshot,uiSettings:{modelOrder:order},$:()=>({}),localStorage:{getItem(){return null;}},sessionStorage:{getItem(){return null;}}};vm.createContext(ctx);
 vm.runInContext(part('function orderedModels','function renderModelSelection'),ctx);vm.runInContext(part('function fallbackCandidates','function renderFallbackSettings'),ctx);
 function rows(settings){const selected=ctx.fallbackCandidates({},'text',settings);return Array.from(ctx.fallbackModelGroups({},'text',settings),g=>g.variants.find(m=>selected.includes(m.id))?.id).filter(Boolean);}
 assert.deepEqual(rows({enabled:true}),expected.default,'default UI tiers must equal worker tiers');
 assert.deepEqual(rows({enabled:true,models:snapshot.allowed}),expected.custom,'custom candidate priority and tiers match worker');
 assert.deepEqual(rows({enabled:true,models:[]}),[]);
}
console.log('Fallback parity passed: Python worker and UI use identical default/custom order and tiers.');
