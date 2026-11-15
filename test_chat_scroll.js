const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
// Exercise the actual render path with browser-like clamping after a row changes.
const head=source.indexOf('function render(){'),positionStart=source.indexOf("  const viewport=$('conversation'),readingTop=",head),positionEnd=source.indexOf('\n',positionStart);
const start=source.indexOf('  if(conv?.summary){',head),end=source.indexOf('\nfunction refresh(afterChange',start);
const render=source.slice(positionStart,positionEnd)+'\nviewport.clientHeight=newViewport;\n'+source.slice(start,end).replace(/\n}\s*$/,'');
function fixture({top=2400,height=3000,newHeight=3500,newViewport=600,older=false,scroll=null,changed=true}={}){
  const calls=[],frames=[],viewport={scrollTop:top,scrollHeight:height,clientHeight:600,
    scrollTo(options){calls.push(options);this.scrollTop=Math.max(0,Math.min(options.top,this.scrollHeight-this.clientHeight));}};
  const entry={scroll},target={scrollIntoView:options=>calls.push({target:true,...options}),classList:{add(){}}};
  const ctx={newViewport,conv:{id:'chat',turns:[],page:{has_newer:older}},$:()=>viewport,
    syncMessageRows(){viewport.scrollHeight=newHeight;viewport.scrollTop=Math.min(viewport.scrollTop,newHeight-600);return changed;},
    conversationPages:new Map([['chat',entry]]),pageCacheKey:id=>id,renderedMessageRows:new Map([['turn:assistant', {element:target}]]),
    requestAnimationFrame:fn=>frames.push(fn)};
  vm.createContext(ctx);vm.runInContext('(function(){'+render+'\n})()',ctx);return {calls,frames,viewport,entry};
}
let result=fixture();assert.equal(result.viewport.scrollTop,2900);assert.equal(result.frames.length,0,'follow must finish immediately, without delayed scrolls');
assert.equal(result.calls[0].behavior,'instant');
result=fixture({top:500});assert.equal(result.viewport.scrollTop,500,'reading above live output stays in place');
result=fixture({top:2300});assert.equal(result.viewport.scrollTop,2300,'being 100px above bottom is intentional reading');
result=fixture({older:true});assert.equal(result.viewport.scrollTop,2400,'an old history page must not follow live output');
result=fixture({scroll:'bottom',top:500});assert.equal(result.viewport.scrollTop,2900,'explicit latest-page navigation still works');assert.equal(result.entry.scroll,null);
result=fixture({scroll:'top'});assert.equal(result.viewport.scrollTop,0,'older/newer page opens at its top');
result=fixture({scroll:'turn:turn:assistant'});assert.equal(result.calls[0].target,true);assert.equal(result.calls[0].behavior,'instant');assert.equal(result.calls[0].block,'center');
result=fixture({changed:false});assert.equal(result.calls.length,0,'unchanged polls never scroll');
result=fixture({newViewport:520});assert.equal(result.viewport.scrollTop,2980,'a newly shown running bar retains live follow');
result=fixture({top:500,newViewport:680});assert.equal(result.viewport.scrollTop,500,'composer height changes preserve the reading position');
const css=fs.readFileSync(__dirname+'/style.css','utf8');
assert.ok(!css.includes('scroll-behavior:smooth'));assert.ok(!/animation:(?:spin|pulse|search-hit)/.test(css));
console.log('Chat scrolling passed: instant live follow, reading position, old pages, explicit paging/search, no deferred jumps or idle animation.');
