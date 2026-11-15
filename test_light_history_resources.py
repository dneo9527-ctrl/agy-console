"""Bounded history and on-demand process metrics; isolated fixtures only."""
import http.client
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from bridge import Client
from resources import ResourceMonitor, physical_memory
from server import Console, Handler, ThreadingHTTPServer, DEFAULT_MODEL
from test_server import FAKE

THREAD='11111111-2222-3333-4444-555555555555'


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        binary=self.root/'fake-agy';binary.write_text(FAKE);binary.chmod(0o700)
        self.app=Console(self.root/'data',binary,import_history=False,
                         account_reader=lambda *_a,**_k:{'state':'unknown','email':None})
        self.conv=self.app.bind_codex(THREAD,'长对话')
        stored=self.app.conversation(self.conv['id'])
        stored['turns']=[{'id':format(i,'032x'),'status':'completed','prompt':'private-prompt-'+str(i),
                          'response':'private-reply-'+str(i)+'x'*10000,'model':DEFAULT_MODEL,'kind':'text',
                          'events':[],'created_at':'2026-11-12T00:00:00Z'} for i in range(55)]
        stored['agy_id']='session-test-42';self.app.save()

    def tearDown(self):self.app.close();self.temp.cleanup()

    def test_summary_has_counts_and_busy_state_without_message_bodies(self):
        full=json.dumps(self.app.snapshot());small=json.dumps(self.app.snapshot(summaries=True))
        self.assertLess(len(small),len(full)/30)
        self.assertNotIn('private-prompt-',small);self.assertNotIn('private-reply-',small)
        c=self.app.conversation(self.conv['id']);c['turns'][0].update(status='running',events=['正在处理'])
        summary=self.app.snapshot(summaries=True)['conversations'][0]
        self.assertEqual(summary['turn_count'],55)
        self.assertEqual([t['id'] for t in summary['turns']],[format(0,'032x'),format(54,'032x')])
        self.assertEqual(summary['turns'][0]['events'],['正在处理'])

    def test_cursor_pages_remain_anchored_when_new_turns_arrive(self):
        latest=self.app.conversation_page(self.conv['id'])
        self.assertEqual((latest['page']['start'],latest['page']['end']),(36,55))
        older=self.app.conversation_page(self.conv['id'],before=latest['page']['first_id'])
        self.assertEqual((older['page']['start'],older['page']['end']),(16,35))
        self.app.conversation(self.conv['id'])['turns'].append({'id':format(55,'032x'),'status':'queued','response':''})
        again=self.app.conversation_page(self.conv['id'],before=latest['page']['first_id'])
        self.assertEqual([t['id'] for t in again['turns']],[t['id'] for t in older['turns']])
        self.assertEqual(again['page']['total'],56)
        newer=self.app.conversation_page(self.conv['id'],after=older['page']['last_id'])
        self.assertEqual((newer['page']['start'],newer['page']['end']),(36,55))
        self.assertTrue(newer['page']['has_newer'])

    def test_invalid_limits_and_cursors_are_rejected(self):
        for limit in (0,41):
            with self.assertRaises(ValueError):self.app.conversation_page(self.conv['id'],limit)
        with self.assertRaises(KeyError):self.app.conversation_page(self.conv['id'],before='missing')
        with self.assertRaises(ValueError):self.app.conversation_page(self.conv['id'],before='x',after='y')

    def test_source_filter_precedes_pagination_and_combines_with_branch(self):
        stored=self.app.conversation(self.conv['id'])
        self.app.create_branch(stored['id'],'History',key='history')
        for i,turn in enumerate(stored['turns']):
            turn.update(source='codex' if i%3==0 else 'web',branch='history')
        before=json.dumps(stored,sort_keys=True)
        page=self.app.conversation_page(self.conv['id'],limit=5,source='codex')
        self.assertEqual(page['page']['total'],19);self.assertEqual(page['page']['source'],'codex')
        self.assertEqual([int(t['id'],16) for t in page['turns']],[42,45,48,51,54])
        older=self.app.conversation_page(self.conv['id'],limit=5,before=page['page']['first_id'],source='codex',branch='history')
        self.assertEqual([int(t['id'],16) for t in older['turns']],[27,30,33,36,39])
        self.assertEqual(json.dumps(stored,sort_keys=True),before)
        with self.assertRaises(ValueError):self.app.conversation_page(self.conv['id'],source='web')
        with self.assertRaises(KeyError):self.app.conversation_page(self.conv['id'],source='codex',before=format(1,'032x'))

    def test_http_formats_have_distinct_etags_and_bridge_can_continue_long_chat(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        def get(path,tag=None):
            client=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
            try:
                client.request('GET',path,headers={'If-None-Match':tag} if tag else {})
                res=client.getresponse();return res.status,res.getheader('ETag'),res.read()
            finally:client.close()
        try:
            _,full_tag,_=get('/api/state')
            status,tag,raw=get('/api/state?summary=1',full_tag)
            self.assertEqual(status,200);self.assertNotEqual(tag,full_tag)
            self.assertTrue(json.loads(raw)['summary_mode'])
            self.assertEqual(get('/api/state?summary=1',tag)[0],304)
            page=json.loads(get('/api/conversations/'+self.conv['id']+'?limit=20')[2])
            self.assertEqual(len(page['turns']),20)
            self.assertEqual(get('/api/conversations/'+self.conv['id']+'?limit=41')[0],400)
            stored=self.app.conversation(self.conv['id']);stored['turns'][0]['source']='codex'
            filtered=json.loads(get('/api/conversations/'+self.conv['id']+'?limit=20&source=codex')[2])
            self.assertEqual(len(filtered['turns']),1);self.assertEqual(filtered['page']['source'],'codex')
            self.assertEqual(get('/api/conversations/'+self.conv['id']+'?limit=20&source=invalid')[0],400)
            client=Client('http://127.0.0.1:'+str(httpd.server_port));conv=client.bind(THREAD)
            turn=client.request('/api/conversations/'+conv['id']+'/turns',{'prompt':'followup','model':DEFAULT_MODEL})
            _,done=client.wait(conv['id'],turn['id'],5)
            self.assertEqual(done['status'],'completed',done.get('error'))
            self.assertEqual(len(self.app.state['conversations']),1)
        finally:httpd.shutdown();httpd.server_close();thread.join(3)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.calls=[];self.memory_calls=[];self.now=0
        self.rows=[{'pid':pid,'parent_pid':parent,'pgid':group,'rss_mb':4.5,'cpu_percent':0.2,'name':name}
                   for pid,parent,group,name in [(10,1,10,'Python'),(20,10,20,'agy'),(21,20,20,'helper'),
                                                  (22,1,20,'orphan-helper'),(30,1,30,'agy'),(40,1,40,'WebKit'),(50,10,10,'ps')]]
        def read():self.calls.append(True);return self.rows
        def memory(pid,timeout):self.memory_calls.append(pid);return 25.0 if pid==10 else None
        self.monitor=ResourceMonitor(read,memory,lambda:self.now)

    def test_no_sampling_until_requested_and_only_owned_tree_is_included(self):
        self.assertFalse(self.calls)
        result=self.monitor.sample({10:'控制台',20:'Ag 任务'})
        self.assertEqual({r['pid'] for r in result['processes']},{10,20,21,22})
        self.assertEqual(self.memory_calls,[10,20])
        self.assertEqual(result['processes'][0]['footprint_mb'],25)
        self.assertIsNone(next(p for p in result['processes'] if p['pid']==20)['footprint_mb'])

    def test_repeated_reads_are_cached_but_forced_refresh_and_pid_changes_resample(self):
        self.monitor.sample({10:'控制台'})
        cached=self.monitor.sample({10:'控制台'});cached['processes'].clear()
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.monitor.sample({10:'控制台'})['processes'])
        self.monitor.sample({10:'控制台'},force=True);self.assertEqual(len(self.calls),2)
        self.monitor.sample({10:'控制台',20:'Ag 任务'});self.assertEqual(len(self.calls),3)

    def test_exited_process_and_unavailable_physical_memory_do_not_break_snapshot(self):
        self.rows=self.rows[:1]
        def unavailable(*_):raise subprocess.TimeoutExpired('footprint',1)
        self.monitor.memory_reader=unavailable
        result=self.monitor.sample({10:'控制台',999:'Ag 任务'})
        self.assertEqual(len(result['processes']),1)
        self.assertIsNone(result['processes'][0]['footprint_mb'])
        self.assertEqual(result['processes'][0]['rss_mb'],4.5)

    def test_current_footprint_is_parsed_separately_from_peak(self):
        fake=subprocess.CompletedProcess([],0,'phys_footprint: 20 MB\nphys_footprint_peak: 80 MB\n','')
        with patch('resources.subprocess.run',return_value=fake):
            self.assertEqual(physical_memory(10,1),21.0)

    def test_live_summary_only_counts_owned_rss_and_never_calls_footprint(self):
        value = self.monitor.summary({10:'控制台',20:'任务'})
        self.assertEqual(value['rss_total_mb'],18.0)
        self.assertEqual(value['process_count'],4)
        self.assertEqual(self.memory_calls,[])
        value['rss_total_mb'] = 0
        self.assertEqual(self.monitor.summary({10:'控制台',20:'任务'})['rss_total_mb'],18.0)
        self.assertEqual(len(self.calls),1)
        self.monitor.summary({10:'控制台',20:'任务'},force=True)
        self.assertEqual(len(self.calls),2)

    def test_live_summary_includes_more_than_detail_row_limit(self):
        self.rows = [dict(self.rows[0],pid=10+i,parent_pid=10) for i in range(50)]
        value = self.monitor.summary({10:'控制台'})
        self.assertEqual(value['process_count'],50)
        self.assertEqual(value['rss_total_mb'],225.0)
        self.assertEqual(self.memory_calls,[])

    def test_unavailable_summary_is_unknown_instead_of_zero(self):
        def unavailable():raise OSError('unavailable')
        self.monitor.reader=unavailable
        value=self.monitor.summary({10:'控制台'})
        self.assertIsNone(value['rss_total_mb'])
        self.assertTrue(value['warning'])


if __name__=='__main__':unittest.main(verbosity=2)
