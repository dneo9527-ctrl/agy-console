"""Image artifact behavior checks: fake CLI and local files, no model calls."""
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from image_files import collect_images, manifest_file
from server import BUSY, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer


PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jpWQAAAAASUVORK5CYII='
PNG = base64.b64decode(PNG_B64)
TURN_ID = 'a' * 32

FAKE_CLI = '''#!/usr/bin/python3
import base64, json, sys
from pathlib import Path
args = sys.argv[1:]
prompt = args[args.index('-p') + 1]
tag = prompt.splitlines()[0]
mode = args[args.index('--mode') + 1]
Path('arguments.json').write_text(json.dumps({'mode':mode, 'prompt':prompt}))
def emit(event):
    print(json.dumps(event, ensure_ascii=False), flush=True)
emit({'event':'init', 'conversation_id':'11111111-2222-3333-4444-555555555555'})
if tag in ('make-image', 'image-only'):
    assert mode == 'accept-edits'
    Path('result.png').write_bytes(base64.b64decode(''' + repr(PNG_B64) + '''))
    response = '' if tag == 'image-only' else '![生成图片](result.png)'
elif tag == 'refuse':
    response = '图片生成不可用，没有生成图片文件。'
else:
    assert mode == 'plan'
    response = '文字任务正常。'
emit({'event':'result', 'result':{'status':'SUCCESS', 'response':response,
    'conversation_id':'11111111-2222-3333-4444-555555555555'}})
'''


class ImageCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.artifacts = self.root / 'session-artifacts'
        self.artifacts.mkdir()
        self.destination = self.root / 'copied-images'

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, response='', started=None):
        return collect_images(self.workspace, self.artifacts, self.destination, TURN_ID,
                              response, time.time() if started is None else started)

    def test_local_file_is_hashed_deduplicated_and_preserved(self):
        original = self.workspace / '蓝色 图片.png'
        original.write_bytes(PNG)
        (self.artifacts / 'duplicate.png').write_bytes(PNG)
        images = self.collect('![图](<蓝色 图片.png>)\n`' + str(original) + '`')
        self.assertEqual(len(images), 1)
        image = images[0]
        self.assertEqual(image['id'], hashlib.sha256(PNG).hexdigest())
        self.assertEqual((image['mime'], image['width'], image['height']), ('image/png', 1, 1))
        self.assertEqual(image['bytes'], len(PNG))
        self.assertEqual(image['url'], '/api/turns/' + TURN_ID + '/images/' + image['id'])
        self.assertEqual(image['download_url'], image['url'] + '?download=1')
        original.unlink()
        self.assertEqual(manifest_file(self.destination, image).read_bytes(), PNG)

    def test_outside_paths_symlinks_remote_and_svg_are_not_collected(self):
        outside = self.root / 'outside.png'
        outside.write_bytes(PNG)
        (self.workspace / 'symlink.png').symlink_to(outside)
        (self.workspace / 'linked-directory').symlink_to(self.root, target_is_directory=True)
        (self.workspace / 'unsafe.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
        (self.workspace / 'not-an-image.png').write_text('this is plain text, not a PNG image')
        response = ('![outside](' + str(outside) + ')\n![traversal](../outside.png)\n'
                    '![link](symlink.png)\n![remote](https://example.invalid/photo.png)\n'
                    '![data](data:image/png;base64,' + PNG_B64 + ')\n![svg](unsafe.svg)')
        self.assertEqual(self.collect(response), [])
        self.assertFalse(self.destination.exists())

    def test_old_file_is_not_mistaken_for_new_generation(self):
        old = self.workspace / 'previous.png'
        old.write_bytes(PNG)
        old_time = time.time() - 60
        os.utime(old, (old_time, old_time))
        self.assertEqual(self.collect('No new image was produced.'), [])

    def test_manifest_cannot_serve_arbitrary_or_symlink_file(self):
        self.destination.mkdir()
        with self.assertRaises(FileNotFoundError):
            manifest_file(self.destination, {'file':'../outside.png'})
        with self.assertRaises(FileNotFoundError):
            manifest_file(self.destination, {'file':'anything.svg'})
        outside = self.root / 'outside.png'
        outside.write_bytes(PNG)
        image_name = 'a' * 64 + '.png'
        (self.destination / image_name).symlink_to(outside)
        with self.assertRaises(FileNotFoundError):
            manifest_file(self.destination, {'file':image_name})


class ImageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fake-agy'
        self.binary.write_text(FAKE_CLI)
        self.binary.chmod(0o700)
        self.app = Console(self.root / 'data', self.binary, import_history=False)
        self.conv = self.app.create()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def run_turn(self, prompt, kind='image'):
        turn = self.app.submit(self.conv['id'], prompt, DEFAULT_MODEL, kind)
        deadline = time.monotonic() + 6
        while turn['status'] in BUSY and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertNotIn(turn['status'], BUSY)
        return turn

    def test_generated_file_is_completed_and_text_mode_stays_plan(self):
        turn = self.run_turn('make-image')
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        self.assertEqual(len(turn['images']), 1)
        image = turn['images'][0]
        file = manifest_file(self.app.data / 'runs' / turn['id'] / 'images', image)
        self.assertEqual(file.read_bytes(), PNG)
        argv_file = self.app.data / 'workspaces' / self.conv['id'] / 'arguments.json'
        self.assertEqual(json.loads(argv_file.read_text())['mode'], 'accept-edits')
        text = self.run_turn('text', kind='text')
        self.assertEqual(text['status'], 'completed')
        self.assertEqual(json.loads(argv_file.read_text())['mode'], 'plan')

    def test_image_only_result_can_complete(self):
        turn = self.run_turn('image-only')
        self.assertEqual(turn['response'], '')
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        self.assertEqual(len(turn['images']), 1)

    def test_success_prose_without_generated_file_is_failed(self):
        turn = self.run_turn('refuse')
        self.assertEqual(turn['status'], 'failed')
        self.assertFalse(turn['images'])
        self.assertTrue(turn['error'])

    def test_immediate_refusal_cannot_reuse_previous_turn_image(self):
        first = self.run_turn('make-image')
        self.assertEqual(first['status'], 'completed')
        second = self.run_turn('refuse')
        self.assertEqual(second['status'], 'failed', 'A refusal reused an earlier turn image')
        self.assertEqual(second['images'], [])

    def test_http_preview_and_download_only_serve_collected_copy(self):
        turn = self.run_turn('make-image')
        self.assertEqual(turn['status'], 'completed')
        image = turn['images'][0]
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def request(path):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            try:
                client.request('GET', path)
                result = client.getresponse()
                return result.status, dict(result.getheaders()), result.read()
            finally:
                client.close()
        try:
            (self.app.data / 'workspaces' / self.conv['id'] / 'result.png').unlink()
            status, headers, content = request(image['url'])
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'image/png')
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertTrue(headers['Content-Disposition'].startswith('inline'))
            self.assertEqual(content, PNG)
            status, headers, content = request(image['download_url'])
            self.assertEqual(status, 200)
            self.assertTrue(headers['Content-Disposition'].startswith('attachment'))
            self.assertEqual(content, PNG)
            self.assertEqual(request(image['url'].replace(image['id'], '0' * 64))[0], 404)
            self.assertEqual(request('/api/turns/' + turn['id'] + '/images/../../state.json')[0], 404)
            self.assertEqual(request('/data/state.json')[0], 404)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
