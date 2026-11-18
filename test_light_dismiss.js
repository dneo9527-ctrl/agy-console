const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8'),events={};let closed=0;
const dialog={getBoundingClientRect:()=>({left:20,top:20,right:200,bottom:200}),addEventListener:(name,fn)=>events[name]=fn,close(){closed++;events.close();}};
const ctx={};vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function installLightDismiss'),source.indexOf('function hideQuickTooltip')),ctx);ctx.installLightDismiss(dialog);
const fire=(name,x,y,target=dialog,button=0)=>events[name]({target,button,clientX:x,clientY:y});
fire('pointerdown',5,5);fire('pointerup',5,5);assert.equal(closed,1);
fire('pointerdown',60,60);fire('pointerup',60,60);assert.equal(closed,1);
fire('pointerdown',60,60,{});fire('pointerup',5,5);assert.equal(closed,1);
fire('pointerdown',5,5);fire('pointerup',60,60);assert.equal(closed,1);
fire('pointerdown',5,5,dialog,2);fire('pointerup',5,5,dialog,2);assert.equal(closed,1);
fire('pointerdown',5,5);events.pointercancel();fire('pointerup',5,5);assert.equal(closed,1);
console.log('Light-dismiss checks passed: outside tap closes, inside controls and padding stay open, dragging out and pointer cancellation never dismiss.');

// Subpanels must not move a pressed target before its click is dispatched.
{
  const controls=new Map();let renders=0,loads=0;
  const control=id=>{if(!controls.has(id))controls.set(id,{events:{},dataset:{},addEventListener(name,fn){(this.events[name]??=[]).push(fn);}});return controls.get(id);};
  const panelCtx={document:{querySelectorAll:()=>[]},$:control,multiAIExpanded:true,consoleStartupExpanded:false,consoleLauncherDirty:false,renderSettings(){renders++;},loadConsoleStartup(){loads++;},saveConsoleLauncher(){}};
  vm.createContext(panelCtx);vm.runInContext(source.slice(source.indexOf("$('multi-ai-expand').addEventListener"),source.indexOf("$('settings-auto-open').addEventListener")),panelCtx);
  const emit=(control,name,target)=>{for(const fn of control.events[name]||[])fn({target});};
  const press=(id,section)=>{
    const target=control(id);target.closest=selector=>selector===section?target:null;
    const before=renders;emit(target,'pointerdown',target);emit(control('settings-dialog'),'pointerdown',target);
    assert.equal(renders,before,'pointerdown must not collapse a panel and move the pressed control');
    emit(target,'pointerup',target);emit(control('settings-dialog'),'pointerup',target);
    emit(target,'click',target);emit(control('settings-dialog'),'click',target);
  };
  press('console-startup-expand','#console-startup-section');
  assert.equal(panelCtx.multiAIExpanded,false);assert.equal(panelCtx.consoleStartupExpanded,true,'one click opens startup after the upper panel closes');assert.equal(loads,1);
  press('multi-ai-expand','#multi-ai-section');
  assert.equal(panelCtx.multiAIExpanded,true,'reverse transition opens collaboration on first click');assert.equal(panelCtx.consoleStartupExpanded,false);
  press('multi-ai-expand','#multi-ai-section');assert.equal(panelCtx.multiAIExpanded,false,'same header still collapses');
  press('console-startup-expand','#console-startup-section');
  press('console-launcher-path','#console-startup-section');assert.equal(panelCtx.consoleStartupExpanded,true,'inside input preserves expanded panel');
  let applied=false;control('other-setting').addEventListener('click',()=>{applied=true;assert.equal(panelCtx.consoleStartupExpanded,true,'the clicked setting acts before outside collapse');});
  press('other-setting','#other-section');assert.equal(applied,true);assert.equal(panelCtx.consoleStartupExpanded,false,'outside click collapses after target action');
  const keyboardTarget=control('multi-ai-expand');keyboardTarget.closest=selector=>selector==='#multi-ai-section'?keyboardTarget:null;
  emit(keyboardTarget,'click',keyboardTarget);emit(control('settings-dialog'),'click',keyboardTarget);assert.equal(panelCtx.multiAIExpanded,true,'keyboard activation opens on a click without pointer events');
  console.log('Settings subpanel click ordering passed: no pointerdown layout shift, both single-click transitions, inner controls, outside action and keyboard activation.');
}
