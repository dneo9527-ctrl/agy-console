'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const {performance}=require('node:perf_hooks');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const between=(start,end)=>{
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,'parser extraction markers remain available');
  return source.slice(first,last);
};
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx={escape,codeCopies:new Map()};
vm.createContext(ctx);
vm.runInContext(between('function localImageBasename(value)','function turnImages(turn)'),ctx);
vm.runInContext(between('function markdown(source','function conversationMeta'),ctx);
const anchor=(label,target)=>`<a href="${escape(target)}" target="_blank" rel="noopener noreferrer">${label}</a>`;

if(process.argv[2]==='--benchmark-child'){
  const results=[];
  const cases=[
    ['unclosed-brackets',n=>'['.repeat(n),'imageReferenceText'],
    ['inline-brackets',n=>'['.repeat(n),'inline'],
    ['incomplete-links',n=>'[a]('.repeat(Math.ceil(n/4)),'inline'],
    ['shared-closing-parenthesis',n=>'[x](/'.repeat(Math.ceil(n/5))+')','inline'],
    ['incomplete-images',n=>'!['.repeat(Math.ceil(n/2)),'markdown'],
    ['backticks',n=>'`'.repeat(n),'inline'],
    ['unclosed-code',n=>'`'+'a'.repeat(n),'inline'],
    ['stars',n=>'*'.repeat(n),'inline'],
    ['unclosed-bold',n=>'**'+'a'.repeat(n),'inline'],
    ['bold-unclosed-brackets',n=>'**'+'['.repeat(n),'inline'],
    ['bold-incomplete-links',n=>'**'+'[a]('.repeat(Math.ceil(n/4)),'inline'],
    ['table-whitespace',n=>'a|b\n'+' '.repeat(n)+'x','markdown'],
    ['nested-url-parentheses',n=>'[manual](https://example.test/'+'('.repeat(Math.floor(n/2))+'x'+')'.repeat(Math.floor(n/2))+')','inline'],
  ];
  // Child-process timeout in the parent bounds even a regressed parser that never returns.
  for(const [name,build,method] of cases){
    const input=build(192000),started=performance.now(),output=ctx[method](input,'large-input');
    const ms=performance.now()-started;
    assert.equal(typeof output,'string');
    assert.ok(ms<2500,`${name} exceeded the generous per-operation bound (${ms.toFixed(1)} ms)`);
    if(name==='unclosed-brackets'||name==='inline-brackets')assert.equal(output,input);
    if(name==='nested-url-parentheses')assert.equal(output,anchor('manual',input.slice(9,-1)));
    results.push({name,bytes:input.length,ms:Number(ms.toFixed(3))});
  }
  const baseline=[];
  for(const size of [12000,24000,48000,96000,192000]){
    const input='['.repeat(size),times=[];
    for(let iteration=0;iteration<7;iteration++){
      const started=performance.now();assert.equal(ctx.imageReferenceText(input),input);times.push(performance.now()-started);
    }
    times.sort((a,b)=>a-b);baseline.push({size,median_ms:Number(times[3].toFixed(3))});
  }
  console.log(JSON.stringify({large_inputs:results,unclosed_bracket_medians:baseline}));
  process.exit(0);
}

for(const [input,expected] of [
  ['/Users/local/output.png','output.png'],
  [' <file:///Users/local/hello%20world.PNG?download=1#view> ','hello world.PNG'],
  ['~/Pictures/example.jpeg','example.jpeg'],
  ['C:\\Users\\local\\picture.webp','picture.webp'],
  ['D:/Pictures/picture.AVIF?download=1','picture.AVIF'],
  ['%2Fprivate%2Fimage.gif','image.gif'],
  ['/private/bad%ZZ.bmp','bad%ZZ.bmp'],
  ['https://example.test/image.png',null],
  ['relative/image.png',null],
  ['/private/document.pdf',null],
])assert.equal(ctx.localImageBasename(input),expected,input);
assert.equal(ctx.imageReferenceText('Image: `/private/a.png` [open](/private/a.png) [/private/b.png](/private/b.png)'),
  'Image: `a.png` open b.png');
assert.equal(ctx.imageReferenceText('[](/private/picture.png?download=1)'), 'picture.png');
assert.equal(ctx.imageReferenceText('![alt](/private/picture.png)'), '!alt');
assert.equal(ctx.imageReferenceText('[web](https://example.test/image.png)'), '[web](https://example.test/image.png)');
assert.equal(ctx.inline('`/private/preview.png`'),'<code>preview.png</code>');
assert.equal(ctx.inline('**bold** and `literal`'),'<strong>bold</strong> and <code>literal</code>');
assert.equal(ctx.inline('[docs](https://example.test/path?q=1&mode=2)'),anchor('docs','https://example.test/path?q=1&mode=2'));
assert.equal(ctx.inline('[manual](https://example.test/a_(b)/c_(d))'),anchor('manual','https://example.test/a_(b)/c_(d)'));
assert.equal(ctx.inline('[**docs**](https://example.test)'),anchor('<strong>docs</strong>','https://example.test'));
assert.equal(ctx.inline('**[docs](https://example.test)**'),'<strong>'+anchor('docs','https://example.test')+'</strong>');
assert.equal(ctx.inline('[`docs`](https://example.test)'),anchor('<code>docs</code>','https://example.test'));
const literal='**bold** [link](https://example.test/a_(b)) <img/src=x/onerror=alert(1)>';
assert.equal(ctx.inline('`'+literal+'`'),'<code>'+escape(literal)+'</code>','inline code remains opaque');
assert.equal(ctx.inline('`[image](/private/picture.png)`'),'<code>[image](/private/picture.png)</code>');
assert.equal(ctx.inline('**`[link](https://example.test)`**'),'<strong><code>[link](https://example.test)</code></strong>');
assert.equal(ctx.inline('**`literal ** stars`**'),'<strong><code>literal ** stars</code></strong>','bold closes outside opaque code');
assert.equal(ctx.inline('[docs](https://example.test/**bold**/`code`)'),anchor('docs','https://example.test/**bold**/`code`'),'destinations are never re-parsed');
assert.equal(ctx.inline('**[docs](https://example.test/**bold**/`code`)**'),'<strong>'+anchor('docs','https://example.test/**bold**/`code`')+'</strong>','bold lookahead also preserves complete links');
assert.equal(ctx.inline('<script>alert("x")</script>'),escape('<script>alert("x")</script>'));
for(const target of ['javascript:alert(1)','data:text/html,<svg/onload=alert(1)>','file:///private/secret.txt','https://example.test/\u0000bad','https://example.test/\tbad']){
  const html=ctx.inline('[unsafe]('+target+')');
  assert.doesNotMatch(html,/<a\b|<svg\b|<script\b/,'unsafe destination stays escaped text: '+target);
}
const hostileLabel='<img/src=x/onerror=alert(1)>';
assert.equal(ctx.inline('['+hostileLabel+'](https://example.test)'),anchor(escape(hostileLabel),'https://example.test'));
const hostileTarget='https://example.test/"onmouseover="alert(1)';
assert.equal(ctx.inline('[quote]('+hostileTarget+')'),anchor('quote',hostileTarget));
assert.doesNotMatch(ctx.inline('[quote]('+hostileTarget+')'),/ href="[^"]*"onmouseover=/);
assert.equal(ctx.inline('![diagram](https://example.test/a_(b).png)'),'[图片：diagram]');
assert.equal(ctx.markdown('![diagram](/private/a_(b).png)','images'),'<p>[图片：diagram]</p>');
assert.equal(ctx.markdown('![](https://example.test/image.png)','images'),'<p>[图片]</p>');
const blocks=ctx.markdown('# Title\n\n- **item**\n- `code`\n\n1. first\n2) second\n\n---\n\n| A | B |\n| --- | :--- |\n| [docs](https://example.test) | <raw> |','blocks');
assert.match(blocks,/<h2>Title<\/h2>/);
assert.match(blocks,/<ul><li><strong>item<\/strong><\/li><li><code>code<\/code><\/li><\/ul>/);
assert.match(blocks,/<ol><li>first<\/li><li>second<\/li><\/ol>/);
assert.match(blocks,/<hr>/);
assert.match(blocks,/<table><thead><tr><th>A<\/th><th>B<\/th><\/tr><\/thead><tbody>/);
assert.match(blocks,/<td>&lt;raw&gt;<\/td>/);
const code='[link](https://example.test)\n**bold**\n<script>bad()</script>\n/private/picture.png';
const key='turn" onclick="bad';
const fenced=ctx.markdown('```html\n'+code+'\n```',key);
assert.equal(ctx.codeCopies.get(key+'-5'),code,'copy-code payload retains original bytes');
assert.match(fenced,/<pre><code>\[link\]\(https:\/\/example.test\)\n\*\*bold\*\*\n&lt;script&gt;bad\(\)&lt;\/script&gt;\n\/private\/picture.png<\/code><\/pre>/);
assert.ok(fenced.includes('data-code="turn&quot; onclick=&quot;bad-5"'),'copy-code key is attribute escaped');
assert.doesNotMatch(fenced,/data-code="turn" onclick=/);
assert.equal(ctx.markdown('```<img src=x>\nraw','unclosed').includes('&lt;img src=x&gt;'),true,'unclosed fences still render escaped content');

const child=spawnSync(process.execPath,[__filename,'--benchmark-child'],{encoding:'utf8',timeout:8000,maxBuffer:1024*1024});
assert.equal(child.error,undefined,'large malformed inputs must complete within a bounded child process: '+String(child.error));
assert.equal(child.status,0,child.stderr||child.stdout);
const benchmark=JSON.parse(child.stdout);
assert.equal(benchmark.large_inputs.length,13);
console.log('Markdown fluency passed: local image names, opaque inline code, safe HTTP(S), balanced URL parentheses, HTML escaping, lists/tables/fences/copy code, and 13 bounded large malformed-input checks.');
console.log('Unclosed bracket medians (ms): '+benchmark.unclosed_bracket_medians.map(row=>row.size+'='+row.median_ms).join(', '));
