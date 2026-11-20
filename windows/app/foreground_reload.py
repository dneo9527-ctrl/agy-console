"""Preflight source validation for an explicitly requested foreground reload."""
from pathlib import Path

PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
              'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')
REQUIRED_FILES = ('VERSION', 'release-source.json', 'update_bootstrap.py', 'software_updates.py', 'server.py', 'bridge.py', 'task_queue.py', 'task_progress.py', 'collaboration_review.py', 'foreground_reload.py',
                  'account_status.py', 'activity_monitor.py', 'resources.py', 'proxy_env.py', 'headless_compat.py',
                  'model_policy.py', 'multi_ai_collaboration.py', 'quota_failover.py', 'conversation_sessions.py', 'image_files.py', 'settings_insights.py',
                  'panel_actions.py', 'panel_backups.py', 'transcript_export.py', 'index.html', 'app.js', 'panel_actions.js', 'style.css', 'icon.svg')


def validate_sources(root):
    root = Path(root)
    total = 0
    for name in REQUIRED_FILES:
        if not (root / name).is_file():
            raise ValueError('更新文件缺失：' + name + '；当前服务继续运行。')
    paths = [path for path in root.glob('*.py') if not path.name.startswith('test_')]
    if len(paths) > 80:
        raise ValueError('更新文件数量异常，未重载。')
    for path in paths:
        try:
            size = path.stat().st_size
            total += size
            if size > 2 * 1024 * 1024 or total > 8 * 1024 * 1024:
                raise ValueError('更新文件过大，未重载。')
            compile(path.read_text(encoding='utf-8'), str(path), 'exec')
        except (OSError, SyntaxError, UnicodeError) as error:
            raise ValueError('更新语法检查未通过：' + path.name + '；当前服务继续运行。') from error


def reload_environment(current, original_proxy):
    result = dict(current)
    for key in PROXY_KEYS:
        if original_proxy.get(key) is None:
            result.pop(key, None)
        else:
            result[key] = original_proxy[key]
    return result
