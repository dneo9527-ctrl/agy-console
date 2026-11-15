const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
let submits=0;const ctx={$:id=>{assert.equal(id,'composer');return{requestSubmit(){submits++;}};}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function composerKeydown'),source.indexOf("$('prompt').addEventListener('keydown'")),ctx);
const press=(overrides={})=>{const event={key:'Enter',shiftKey:false,altKey:false,isComposing:false,keyCode:13,repeat:false,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...overrides};ctx.composerKeydown(event);return event;};
assert.equal(press().defaultPrevented,true);assert.equal(submits,1);
for(const key of [{shiftKey:true},{isComposing:true},{keyCode:229},{altKey:true},{key:'a'},{defaultPrevented:true}]){
  const before=submits;press(key);assert.equal(submits,before);
}
const repeated=press({repeat:true});assert.equal(repeated.defaultPrevented,true);assert.equal(submits,1);
press({ctrlKey:true});assert.equal(submits,2);
assert.ok(!source.includes("event.key==='Enter'){event.preventDefault();$('composer').requestSubmit();}"));
console.log('Composer keys passed: Enter sends once, Shift+Enter stays multiline, IME confirmation and key repeat never submit.');
