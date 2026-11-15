"""Public GitHub program-only updates, staged while idle, applied at next start."""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import threading
import time
from urllib.parse import quote, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler
import zipfile
from update_bootstrap import FILES, MAX_BYTES, atomic_json, platform_target, verify_stage, version_key

REPO=re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}')


def safe_url(url):
    p=urlsplit(url)
    if (p.scheme!='https' or p.username or p.password or p.port not in (None,443)
            or p.hostname not in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}):
        raise ValueError('更新仅允许 GitHub 官方 HTTPS 发布地址。')
    return url


class Redirects(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class SoftwareUpdates:
    def __init__(self,root,data,idle=lambda:False,notify=lambda:None,fetch=None,start=True):
        self.root=Path(root);self.cache=Path(data)/'software-updates';self.cache.mkdir(exist_ok=True)
        self.idle,self.notify=idle,notify
        self.fetch=fetch or self.download
        self.current=(self.root/'VERSION').read_text(encoding='utf-8').strip();version_key(self.current)
        self.target=platform_target();self.lock=threading.RLock();self.stop_event=threading.Event();self.wake=threading.Event()
        source=self.root/'release-source.json';defaults=json.loads(source.read_text(encoding='utf-8')) if source.exists() else {}
        prefs=self.cache/'preferences.json'
        self.preferences={'repository':defaults.get('repository',''),'channel':defaults.get('channel','stable'),'automatic':False}
        if prefs.exists():self.preferences.update(json.loads(prefs.read_text(encoding='utf-8')))
        self.validate_preferences(self.preferences)
        self.state={'status':'idle','checked_at':None,'latest':None,'error':''};self.requested=None
        stamp=self.cache/'last-check.json'
        try:
            saved=json.loads(stamp.read_text(encoding='utf-8')) if stamp.is_file() and stamp.stat().st_size<4096 else {}
            if (saved.get('repository')==self.preferences['repository'] and saved.get('channel')==self.preferences['channel']
                    and type(saved.get('checked_at')) in (int,float) and 0<=time.time()-saved['checked_at']<86400):self.state['checked_at']=saved['checked_at']
        except (OSError,ValueError):pass
        self.thread=None
        if start:self.start()

    def start(self):
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    @staticmethod
    def validate_preferences(value):
        if (not isinstance(value,dict) or set(value)!={'repository','channel','automatic'}
                or not isinstance(value['repository'],str) or value['repository'] and not REPO.fullmatch(value['repository'])
                or value['channel'] not in ('stable','preview') or type(value['automatic']) is not bool):
            raise ValueError('更新源需为 GitHub OWNER/REPO，通道为稳定或预览。')

    def snapshot(self):
        with self.lock:
            result=dict(self.state,current=self.current,target=self.target,preferences=dict(self.preferences))
            pending=self.cache/'pending.json'
            try:
                if pending.is_symlink() or pending.exists() and pending.stat().st_size>4096:raise ValueError('invalid pending')
                result['pending_version']=json.loads(pending.read_text(encoding='utf-8')).get('version') if pending.exists() else None
            except (OSError,ValueError,AttributeError):
                result.update(pending_version=None,status='error',error='暂存更新状态异常，请从原启动文件重开以恢复。')
            if result['pending_version']:result['status']='staged'
            return copy.deepcopy(result)

    def set_preferences(self,value):
        self.validate_preferences(value)
        with self.lock:
            if self.state['status'] in ('checking','downloading'):raise ValueError('正在检查或下载，请稍后更改更新设置。')
            changed=(value['repository'],value['channel'])!=(self.preferences['repository'],self.preferences['channel'])
            if changed:
                (self.cache/'pending.json').unlink(missing_ok=True);self.state.update(latest=None,checked_at=None,error='',status='idle')
            self.preferences=dict(value);atomic_json(self.cache/'preferences.json',value)
        self.wake.set();self.notify();return self.snapshot()

    def request(self,action):
        if action not in ('check','download'):raise ValueError('更新操作无效。')
        with self.lock:
            if not self.preferences['repository']:raise ValueError('发布源尚未配置，请先填写该项目的 GitHub 仓库。')
            if self.requested or self.state['status'] in ('checking','downloading'):raise ValueError('更新检查正在进行。')
            if action=='download' and not self.state['latest']:raise ValueError('请先检查更新。')
            self.requested=action
            self.state.update(status='checking' if action=='check' else 'downloading',error='')
        self.wake.set();self.notify();return self.snapshot()

    def download(self,url,path,limit):
        safe_url(url)
        from urllib.request import Request
        request=Request(url,headers={'Accept':'application/vnd.github+json' if url.startswith('https://api.github.com/') else 'application/octet-stream',
                                     'User-Agent':'AgyConsole-Updater/'+self.current})
        size=0
        with build_opener(Redirects()).open(request,timeout=15) as response,path.open('wb') as stream:
            safe_url(response.url)
            while True:
                if self.stop_event.is_set():raise ValueError('控制台关闭，更新下载已停止。')
                chunk=response.read(65536)
                if not chunk:break
                size+=len(chunk)
                if size>limit:raise ValueError('更新附件超过大小限制。')
                stream.write(chunk)

    def read_remote_json(self,url,name,limit):
        path=self.cache/name
        try:self.fetch(url,path,limit);return json.loads(path.read_text(encoding='utf-8'))
        finally:path.unlink(missing_ok=True)

    def check(self):
        with self.lock:
            prefs=dict(self.preferences);self.state.update(status='checking',error='')
        self.notify();repo=prefs['repository']
        releases=self.read_remote_json('https://api.github.com/repos/'+repo+'/releases?per_page=20','releases.json',1024*1024)
        if not isinstance(releases,list):raise ValueError('发布列表无效。')
        candidates=[]
        for release in releases[:20]:
            if not isinstance(release,dict) or release.get('draft') or prefs['channel']=='stable' and release.get('prerelease'):continue
            tag=release.get('tag_name','');version=tag[1:] if tag.startswith('v') else tag
            try:key=version_key(version)
            except ValueError:continue
            if prefs['channel']=='stable' and '-' in version:continue
            if key>version_key(self.current):candidates.append((key,version,tag,release))
        latest=None
        for _,version,tag,release in sorted(candidates,reverse=True):
            assets=release.get('assets') or []
            if not any(a.get('name')=='updates.json' for a in assets if isinstance(a,dict)):continue
            base='https://github.com/'+repo+'/releases/download/'+quote(tag,safe='')+'/'
            manifest=self.read_remote_json(base+'updates.json','manifest.json',65536)
            if manifest.get('schema')!=1 or manifest.get('version')!=version:raise ValueError('发布更新清单版本不匹配。')
            asset=(manifest.get('platforms') or {}).get(self.target)
            if asset is None:continue
            if (not isinstance(asset,dict) or not re.fullmatch(r'[A-Za-z0-9_.-]+\.zip',str(asset.get('file','')))
                    or not re.fullmatch('[a-f0-9]{64}',str(asset.get('sha256','')))
                    or type(asset.get('bytes')) is not int or not 0<asset['bytes']<=MAX_BYTES):raise ValueError('平台更新附件无效。')
            published=next((a for a in assets if a.get('name')==asset['file']),None)
            if not published or published.get('size')!=asset['bytes']:raise ValueError('更新附件与公开发布不一致。')
            if published.get('digest') and published['digest']!='sha256:'+asset['sha256']:raise ValueError('GitHub附件摘要不匹配。')
            latest=dict(asset,version=version,url=base+asset['file'],release_url='https://github.com/'+repo+'/releases/tag/'+quote(tag,safe=''))
            break
        with self.lock:self.state.update(status='available' if latest else 'current',latest=latest,checked_at=time.time(),error='')
        atomic_json(self.cache/'last-check.json',{'repository':repo,'channel':prefs['channel'],'checked_at':self.state['checked_at']})
        self.notify();return self.snapshot()

    def stage(self):
        if not self.idle():raise ValueError('请等待全部模型任务与账号检查结束后下载更新。')
        with self.lock:
            latest=copy.deepcopy(self.state['latest']);self.state.update(status='downloading',error='')
        if not latest:raise ValueError('没有待下载的更新。')
        self.notify();archive=self.cache/'download.zip';stage=self.cache/'stage';temporary=self.cache/'stage-new'
        if temporary.exists():shutil.rmtree(temporary)
        temporary.mkdir()
        try:
            self.fetch(latest['url'],archive,latest['bytes'])
            if archive.stat().st_size!=latest['bytes'] or hashlib.sha256(archive.read_bytes()).hexdigest()!=latest['sha256']:raise ValueError('更新包SHA256校验失败。')
            with zipfile.ZipFile(archive) as z:
                entries=z.infolist();names=[i.filename for i in entries]
                if (len(names)>len(FILES)+1 or len(set(names))!=len(names) or not set(names).issubset(FILES|{'payload.json'})
                        or sum(i.file_size for i in entries)>MAX_BYTES):raise ValueError('更新包路径、数量或体积无效。')
                for item in entries:
                    if stat.S_ISLNK(item.external_attr>>16) or item.flag_bits&1 or item.is_dir():raise ValueError('更新包含不允许的文件。')
                    with z.open(item) as source,(temporary/item.filename).open('xb') as output:
                        copied=0
                        while True:
                            chunk=source.read(65536)
                            if not chunk:break
                            copied+=len(chunk)
                            if copied>MAX_BYTES:raise ValueError('解压体积超过限制。')
                            output.write(chunk)
            verify_stage(temporary,self.target,latest['version'])
            if stage.exists():shutil.rmtree(stage)
            temporary.rename(stage)
            atomic_json(self.cache/'pending.json',{'version':latest['version'],'target':self.target})
            with self.lock:self.state.update(status='staged',error='')
        finally:
            archive.unlink(missing_ok=True)
            if temporary.exists():shutil.rmtree(temporary)
        self.notify();return self.snapshot()

    def run(self):
        while not self.stop_event.is_set():
            try:
                with self.lock:
                    action=self.requested;self.requested=None;prefs=dict(self.preferences)
                    checked=self.state['checked_at'];latest=copy.deepcopy(self.state['latest']);status=self.state['status']
                    if not action and prefs['repository'] and (checked is None or time.time()-checked>86400):
                        action='check';self.state.update(status='checking',error='')
                # Reserve an automatic action before entering application callbacks.
                # Imports then either finish first, or see checking/downloading and wait.
                if (not action and prefs['automatic'] and latest and status=='available'
                        and not (self.cache/'pending.json').exists() and self.idle()):
                    with self.lock:
                        if self.preferences==prefs and self.state['status']=='available' and self.state['latest']==latest:
                            action='download';self.state.update(status='downloading',error='')
                if action=='check':self.check()
                elif action=='download':self.stage()
            except Exception as error:
                with self.lock:self.state.update(status='error',error=str(error)[:250],checked_at=time.time())
                try:atomic_json(self.cache/'last-check.json',{'repository':prefs['repository'],'channel':prefs['channel'],'checked_at':self.state['checked_at']})
                except OSError:pass
                self.notify()
            self.wake.wait(30);self.wake.clear()

    def close(self):
        self.stop_event.set();self.wake.set()
