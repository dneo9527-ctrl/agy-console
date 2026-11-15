// A cached history page must not override the latest quota/model metadata.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync(__dirname+'/app.js','utf8'),part=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)));
const flash='gemini-3.8-flash-high',argon='gemini-4-argon-high';
const meta={id:'a',summary:true,turn_count:40,revision:'unchanged',primary_branch:'main',branches:{main:{id:'main'}},turns:[],model_preferences:{text:flash},fallback_preferences:{text:{enabled:true}},model_resolution:{text:{requested_model:flash,effective_model:flash,automatic:false,fallback_enabled:true}}};
const page={id:'a',summary:false,page:{total:40,has_newer:true},turns:[{id:'old-message'}],model_resolution:{text:{requested_model:flash,effective_model:argon,automatic:true,fallback_enabled:true}},fallback_preferences:{text:{enabled:false}}};
const nodes={model:{value:flash},'task-kind':{value:'text'}};
const ctx={snapshot:{conversations:[meta],preferred_model:argon,service:{features:{model_resolution:true,session_groups:true}},health:{models:[{id:flash},{id:argon}],account:{state:'known',quota:{'gemini-argon-wtu-weekly':{remaining_fraction:0}}}}},activeId:'a',activeBranch:'main',viewMode:'panel',PAGE_SIZE:20,conversationPages:new Map([['a:main',{value:page}]]),$:id=>nodes[id]};
vm.createContext(ctx);vm.runInContext(part('function conversationMeta','function restoredTaskKind'),ctx);vm.runInContext(part('function pageCacheKey','function localConversationPage'),ctx);
ctx.composerConversation=()=>ctx.active();vm.runInContext(part('function quotaGroupsForModel','function orderedModels'),ctx);vm.runInContext(part('function scopedModelValue','const modelEffortLabels'),ctx);
let active=ctx.active();assert.equal(active.model_resolution.text.effective_model,flash);assert.equal(active.fallback_preferences.text.enabled,true);assert.equal(active.summary,false);assert.equal(active.turns[0].id,'old-message');assert.equal(active.page.has_newer,true);
assert.equal(ctx.effectiveComposerModel(ctx.snapshot.health),flash);
// Health changes without a conversation revision change must update the forecast.
meta.model_resolution.text={requested_model:flash,effective_model:argon,automatic:true};assert.equal(ctx.composerModelResolution(),null,'already-exhausted alternative is never shown as usable');
meta.model_resolution.text={requested_model:flash,effective_model:flash,automatic:false};assert.equal(ctx.composerModelResolution().effective_model,flash);
assert.equal(page.model_resolution.text.effective_model,argon,'historical page is preserved instead of being mutated');
console.log('Model forecast cache passed: latest policy metadata wins over old history, exhausted alternatives rejected, transcript and scroll-page state retained.');
