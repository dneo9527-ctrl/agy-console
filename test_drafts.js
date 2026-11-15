const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const ctx={};vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('class DraftStore'),source.indexOf('function makeDraftStore'))+'\nthis.Store=DraftStore;',ctx);
class Storage{constructor(){this.values=new Map();}get length(){return this.values.size;}key(i){return [...this.values.keys()][i]??null;}getItem(k){return this.values.get(k)??null;}setItem(k,v){this.values.set(k,v);}removeItem(k){this.values.delete(k);}}
const storage=new Storage();let now=10000;
const first=new ctx.Store(storage,'first','',()=>now);
first.write('chat-a:main','中文草稿 <tag>');first.write('chat-a:image','图片描述');first.write('chat-b:main','另一个聊天');
const reloaded=new ctx.Store(storage,'reload','first',()=>++now);
assert.equal(reloaded.read('chat-a:main'),'中文草稿 <tag>');assert.equal(reloaded.read('chat-a:image'),'图片描述');assert.equal(reloaded.read('chat-b:main'),'另一个聊天');
const second=new ctx.Store(storage,'second','',()=>++now);second.write('chat-a:main','另一页的新草稿');
first.write('chat-a:main','中文草稿 <tag>'); // An old unchanged page must not gain a new timestamp on blur.
const fresh=new ctx.Store(storage,'fresh','',()=>++now);assert.equal(fresh.read('chat-a:main'),'另一页的新草稿');
reloaded.clearSent('chat-a:main','中文草稿 <tag>');assert.equal(fresh.read('chat-a:main'),'另一页的新草稿');
second.write('chat-a:main','发出后继续输入');second.clearSent('chat-a:main','另一页的新草稿');assert.equal(new ctx.Store(storage,'next','',()=>++now).read('chat-a:main'),'发出后继续输入');
const before=storage.length;first.write('chat-a:main','中文草稿 <tag>');assert.equal(storage.length,before);
first.write('chat-a:main','');assert.equal(new ctx.Store(storage,'after','first',()=>++now).read('chat-a:main'),'');
for(let i=0;i<80;i++){now++;first.write('bulk:'+i,'x'.repeat(8000));}
const saved=first.entries();assert.ok(saved.length<=64);assert.ok(saved.reduce((n,r)=>n+r.text.length,0)<=256000);
now+=8*86400000;assert.equal(new ctx.Store(storage,'expired','',()=>now).read('chat-b:main'),'');
storage.setItem('unrelated','private');storage.setItem('agy-draft-v2:broken','not json');first.prune();assert.equal(storage.getItem('unrelated'),'private');assert.equal(storage.getItem('agy-draft-v2:broken'),null);
storage.setItem=()=>{throw new Error('quota full');};assert.throws(()=>first.write('failure','keep in page'),/quota full/);
console.log('Draft checks passed: reload recovery, chat/session isolation, independent writers, unchanged stale blur, exact submitted-text cleanup, new-input preservation, TTL and size caps, storage failure.');
