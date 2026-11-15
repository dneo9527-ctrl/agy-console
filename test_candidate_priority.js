const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const src=fs.readFileSync(__dirname+'/app.js','utf8'),part=(a,b)=>src.slice(src.indexOf(a),src.indexOf(b,src.indexOf(a)));
const chats={a:{id:'a',fallback_preferences:{text:{enabled:true,models:['sonnet-low','flash-high','argon-high']},image:{enabled:true,models:['argon-high']}}},b:{id:'b',fallback_preferences:{}}};
let owner=chats.a,resolve,calls=[];const nodes=new Map(),node=id=>{if(!nodes.has(id))nodes.set(id,{value:'text',dataset:{},setAttribute(){}});return nodes.get(id);};
const models=['argon-high','flash-high','flash-low','sonnet-low'].map(id=>({id,label:id}));
const ctx={online:true,snapshot:{service:{features:{quota_failover:true}},health:{models}},uiSettings:{modelOrder:[]},settingsOwner:()=>owner,conversationMeta:id=>chats[id],conversationPages:new Map(),pageCacheKey:id=>id,$:node,render(){},toast(){},localStorage:{getItem(){return null;}},api:(path,body)=>{calls.push({path,body});return new Promise(r=>resolve=r);}};
vm.createContext(ctx);vm.runInContext(part('function orderedModels','function renderModelSelection'),ctx);vm.runInContext(part('let fallbackExpanded','function installLightDismiss'),ctx);
(async()=>{
 let settings=chats.a.fallback_preferences.text;
 assert.deepEqual(Array.from(ctx.fallbackModelGroups(owner,'text',settings),g=>g.key),['sonnet','flash','argon']);
 const moving=ctx.moveFallbackModel('flash','up');assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-1).body)),{kind:'text',settings:{enabled:true,models:['flash-high','sonnet-low','argon-high']}});
 owner=chats.b;resolve({id:'a',fallback_preferences:{...chats.a.fallback_preferences,text:calls.at(-1).body.settings}});await moving;
 assert.deepEqual(Array.from(chats.a.fallback_preferences.text.models),['flash-high','sonnet-low','argon-high']);assert.equal(chats.b.fallback_preferences.text,undefined);
 assert.deepEqual(chats.a.fallback_preferences.image.models,['argon-high']);
 owner=chats.a;node('fallback-kind').value='image';const n=calls.length;await ctx.moveFallbackModel('argon','up');assert.equal(calls.length,n,'first row cannot move above list');
 node('fallback-kind').value='text';await ctx.moveFallbackModel('argon','down');assert.equal(calls.length,n,'last row cannot move below list');
 const html=fs.readFileSync(__dirname+'/index.html','utf8');assert.ok(!html.includes('<strong>模型排序</strong>'));assert.ok(html.includes('<strong>额度用完自动候补</strong>'));
 console.log('Candidate priority passed: arrows save ordered tiers, first/last bounds, per-chat/per-kind isolation, old sorting surface removed.');
})().catch(e=>{console.error(e);process.exit(1);});
