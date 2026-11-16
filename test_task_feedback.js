const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/app.js','utf8');
const ctx={escape:s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;'),snapshot:{conversations:[{turns:[{id:'r',status:'running',created_at:'2026-11-13T00:00:00Z'},{id:'q1',status:'queued',created_at:'2026-11-13T00:00:01Z'},{id:'q2',status:'queued',created_at:'2026-11-13T00:00:02Z'},{id:'done',status:'completed'}]}]}};
vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('function taskErrorHTML'),source.indexOf('function panelUserHTML')),ctx);
const q=ctx.queueSummary({id:'q2'});assert.equal(q.running,1);assert.equal(q.queued,2);assert.ok(q.label.includes('第 2 位'));assert.ok(!q.label.includes('连接'));
const denied=ctx.taskErrorHTML('headless mode cannot prompt so command was auto-denied <script>');assert.ok(denied.includes('所需工具需要授权'));assert.ok(denied.includes('<details>'));assert.ok(!denied.includes('<script>'));
const network=ctx.taskErrorHTML('network i/o timeout');assert.ok(network.includes('network i/o timeout'));assert.ok(!network.includes('需要授权'));
assert.ok(network.includes('连接模型服务超时'));assert.ok(network.includes('<details>'));
const capacity=ctx.taskErrorHTML('API error (attempt 1): UNAVAILABLE (code 503): No capacity available <script>');
assert.ok(capacity.includes('模型暂时繁忙（503）'));assert.ok(capacity.includes('<details>'));assert.ok(!capacity.includes('<script>'));
assert.equal(ctx.taskErrorHTML(''),'');
const oldQuota=ctx.taskErrorHTML('工具权限被拒绝：read_file',{
  source:'official_tool_denial',historical_error_step:134,raw_cli_error:'Individual quota reached <script>'
});
assert.ok(oldQuota.includes('<strong>工具权限被拒绝</strong>'));
assert.ok(oldQuota.includes('历史步骤 134'));
assert.ok(oldQuota.indexOf('Individual quota reached')>oldQuota.indexOf('<details>'));
assert.ok(!oldQuota.includes('<script>'));
assert.ok(!oldQuota.includes('模型暂时繁忙'));
console.log('Task feedback passed: queue position is distinct from execution, denied tools remain incomplete, raw diagnostics escaped.');
