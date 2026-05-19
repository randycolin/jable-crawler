#!/usr/bin/env python3
"""
极简内网视频播放网站
简洁、快速、专注
"""

import os
import re
import json
import subprocess
import sqlite3
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, send_from_directory, request, jsonify, abort, redirect, url_for
import socket

app = Flask(__name__)

# 配置
VIDEO_DIR = os.environ.get("VIDEO_DIR", "/root/jable-crawler/videos")
JDL_DIR = os.environ.get("JDL_DIR", os.path.dirname(VIDEO_DIR))
JDL_BIN = os.environ.get("JDL_BIN", "/usr/local/bin/jdl")
PORT = int(os.environ.get("PORT", "8090"))
BIND_HOST = os.environ.get("BIND_HOST", "0.0.0.0")
THUMB_DIR = os.environ.get("THUMB_DIR", "/root/video-simple/static/thumbs")
ALLOWED_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.webm'}

# 获取内网IP
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

HOST = get_local_ip()

def display_title(filename):
    """把下载文件名整理成适合手机列表显示的短标题。"""
    stem = os.path.splitext(filename)[0]
    code = ''
    m = re.match(r'^(?:91porny|jable)_([0-9a-fA-F]{8,}|[A-Za-z0-9-]+)_(.+)$', stem)
    if m:
        code = m.group(1)
        stem = m.group(2)
    else:
        stem = re.sub(r'^(?:91porny|jable)[_-]+', '', stem, flags=re.I)
    stem = re.sub(r'[_\s]+', ' ', stem).strip(' -_')
    if not stem:
        stem = filename
    short_code = code[:8] if code else ''
    return stem, short_code


def make_video_id(rel_path):
    """生成短 ID，播放页只用 ID 查真实 path，避免标题/URL 编码影响播放。"""
    import hashlib
    return hashlib.md5(rel_path.encode()).hexdigest()[:8]


def find_video(video_id):
    """按稳定 ID 查视频。"""
    return next((v for v in get_video_files() if v['id'] == video_id), None)


def video_response_by_id(video_id):
    """按稳定 ID 输出视频文件，避免浏览器处理中文长文件名导致播放失败。"""
    video = find_video(video_id)
    if not video:
        abort(404)
    return send_from_directory(VIDEO_DIR, video['rel_path'])


def thumb_path(video_id):
    return os.path.join(THUMB_DIR, f'{video_id}.jpg')


def generate_thumbnail(video, force=False):
    """用 ffmpeg 从真实视频生成缩略图；失败时返回空串让前端使用兜底渐变。"""
    out = thumb_path(video['id'])
    if os.path.exists(out) and not force and os.path.getsize(out) > 1024:
        return out
    Path(THUMB_DIR).mkdir(parents=True, exist_ok=True)
    src = os.path.join(VIDEO_DIR, video['rel_path'])
    tmp = out + '.tmp.jpg'
    # 取 3 秒附近一帧；很短的视频则 ffmpeg 会自动取可用帧。
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', '3', '-i', src,
        '-frames:v', '1', '-vf', 'scale=480:-1:force_original_aspect_ratio=decrease',
        '-q:v', '3', tmp
    ]
    try:
        subprocess.run(cmd, timeout=20, check=True)
        if os.path.exists(tmp) and os.path.getsize(tmp) > 1024:
            os.replace(tmp, out)
            return out
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
    return ''


def load_db_index():
    """从 jdl 数据库读取已下载视频索引，用 file_path/filename 映射明确站点。"""
    db_path = os.path.join(os.path.dirname(VIDEO_DIR), 'jable.db')
    by_abs = {}
    by_name = {}
    if not os.path.exists(db_path):
        return by_abs, by_name
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT site, code, title, file_path, tags FROM videos WHERE downloaded=1 AND file_path != ''"):
            item = dict(r)
            abs_path = os.path.abspath(item.get('file_path') or '')
            by_abs[abs_path] = item
            by_name[os.path.basename(abs_path)] = item
        conn.close()
    except Exception:
        pass
    return by_abs, by_name


def detect_site(rel_path, filename, db_row=None):
    """明确区分 91PORNY/Jable，优先数据库，其次路径/文件名前缀。"""
    if db_row and db_row.get('site'):
        site = db_row['site'].lower()
        if site == '91porny':
            return '91porny', '91PORNY'
        if site == 'jable':
            return 'jable', 'Jable'
    s = f'{rel_path}/{filename}'.lower()
    if '91porny' in s or filename.lower().startswith('91porny_'):
        return '91porny', '91PORNY'
    return 'jable', 'Jable'


def get_video_files():
    """获取视频文件列表，并明确标注来源站点。"""
    videos = []
    db_by_abs, db_by_name = load_db_index()
    
    def scan_dir(directory, prefix=""):
        if not os.path.exists(directory):
            return []
            
        for item in os.listdir(directory):
            path = os.path.join(directory, item)
            rel_path = os.path.join(prefix, item) if prefix else item
            
            if os.path.isfile(path):
                ext = os.path.splitext(item)[1].lower()
                if ext in ALLOWED_EXTENSIONS:
                    size = os.path.getsize(path)
                    size_mb = size / (1024 * 1024)
                    
                    mtime = os.path.getmtime(path)
                    date_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                    
                    vid = make_video_id(rel_path)
                    abs_path = os.path.abspath(path)
                    db_row = db_by_abs.get(abs_path) or db_by_name.get(item)
                    site, site_label = detect_site(rel_path, item, db_row)
                    title, short_code = display_title(item)
                    if db_row:
                        if db_row.get('title'):
                            title = db_row['title']
                        if db_row.get('code'):
                            short_code = str(db_row['code'])[:8]
                    videos.append({
                        'id': vid,
                        'name': item,
                        'title': title,
                        'code': short_code,
                        'site': site,
                        'site_label': site_label,
                        'rel_path': rel_path,
                        'path': f'/video/{rel_path}',
                        'stream_path': f'/stream/{vid}',
                        'thumb_path': f'/thumb/{vid}.jpg',
                        'size': f'{size_mb:.1f}MB',
                        'size_bytes': size,
                        'date': date_str,
                        'mtime': mtime,
                        'ext': ext[1:].upper(),
                        'folder': os.path.dirname(rel_path) or site_label
                    })
            elif os.path.isdir(path):
                scan_dir(path, rel_path)
    
    scan_dir(VIDEO_DIR)
    # 同一站点经常给不同片段复用同一标题；显示时追加短编号/大小区分。
    title_counts = {}
    for v in videos:
        title_counts[v['title']] = title_counts.get(v['title'], 0) + 1
    for v in videos:
        v['base_title'] = v['title']
        if title_counts.get(v['title'], 0) > 1:
            suffix = v.get('code') or v['id']
            v['title'] = f"{v['title']} · #{suffix} · {v['size']}"
    # 按修改时间倒序
    videos.sort(key=lambda x: x['mtime'], reverse=True)
    return videos

def get_folders():
    """获取所有文件夹"""
    videos = get_video_files()
    folders = set()
    for v in videos:
        folders.add(v['folder'])
    return sorted(list(folders))


def db_path():
    return os.path.join(JDL_DIR, 'jable.db')


def is_jdl_daemon_running():
    """通过 jdl daemon status 判断后台下载是否运行。"""
    try:
        r = subprocess.run([JDL_BIN, 'daemon', 'status'], cwd=JDL_DIR, capture_output=True, text=True, timeout=10)
        return 'running' in (r.stdout or '').lower()
    except Exception:
        return False


def jdl_command(*args):
    """执行 jdl 控制命令，返回结构化结果。"""
    try:
        r = subprocess.run([JDL_BIN, *args], cwd=JDL_DIR, capture_output=True, text=True, timeout=30)
        return {'ok': r.returncode == 0, 'stdout': (r.stdout or '').strip(), 'stderr': (r.stderr or '').strip(), 'code': r.returncode}
    except Exception as e:
        return {'ok': False, 'stdout': '', 'stderr': str(e), 'code': -1}


def get_queue_summary():
    """读取 jdl 队列概览，失败时返回空统计，避免播放器被数据库问题拖垮。"""
    summary = {'pending': 0, 'downloading': 0, 'done': 0, 'failed': 0, 'retrying': 0, 'skipped': 0, 'total': 0, 'daemon': 'running' if is_jdl_daemon_running() else 'stopped'}
    if not os.path.exists(db_path()):
        return summary
    try:
        conn = sqlite3.connect(db_path())
        for status, count in conn.execute("SELECT status, COUNT(*) FROM download_queue GROUP BY status"):
            summary[status] = count
            summary['total'] += count
        conn.close()
    except Exception:
        pass
    return summary


def get_queue_items(limit=50):
    """读取队列明细，给网页控制页展示。"""
    if not os.path.exists(db_path()):
        return []
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT dq.id AS qid, dq.video_id, dq.status, dq.retry_count, dq.error_msg, dq.added_at,
               v.site, v.code, v.title, v.downloaded, v.file_path
        FROM download_queue dq
        JOIN videos v ON v.id = dq.video_id
        ORDER BY CASE dq.status WHEN 'downloading' THEN 0 WHEN 'pending' THEN 1 WHEN 'retrying' THEN 2 WHEN 'failed' THEN 3 ELSE 4 END,
                 dq.added_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    items = []
    for r in rows:
        d = dict(r)
        if d.get('error_msg') and len(d['error_msg']) > 80:
            d['error_msg'] = d['error_msg'][:80] + '...'
        d['site_label'] = '91PORNY' if d.get('site') == '91porny' else 'Jable'
        items.append(d)
    return items


def filter_videos(videos, search='', folder='', view='home'):
    """统一处理首页、分类、搜索、发现/随机、下载页过滤。"""
    filtered = list(videos)
    if folder:
        filtered = [v for v in filtered if v['folder'] == folder]
    if search:
        s = search.lower()
        filtered = [v for v in filtered if s in v['name'].lower() or s in v['title'].lower() or s in v.get('code', '').lower()]
    if view == '91porny':
        filtered = [v for v in filtered if v.get('site') == '91porny']
    elif view == 'jable':
        filtered = [v for v in filtered if v.get('site') == 'jable']
    elif view == 'discover':
        filtered = sorted(filtered, key=lambda v: v['id'])
    else:
        filtered = sorted(filtered, key=lambda v: v['mtime'], reverse=True)
    return filtered


@app.route('/')
def index():
    """首页/推荐/搜索/分类。"""
    all_videos = get_video_files()
    folders = get_folders()
    search = request.args.get('search', '').strip()
    folder = request.args.get('folder', '').strip()
    view = request.args.get('view', 'home').strip() or 'home'
    videos = filter_videos(all_videos, search=search, folder=folder, view=view)
    queue = get_queue_summary()
    return render_template('index.html',
                         videos=videos[:80],
                         folders=folders,
                         total=len(all_videos),
                         filtered_total=len(videos),
                         queue=queue,
                         view=view,
                         search=search,
                         folder=folder)

@app.route('/video/<path:filename>')
def serve_video(filename):
    """兼容旧 URL：直接按相对路径提供视频文件。"""
    safe_path = os.path.normpath(filename).lstrip('/')
    if '..' in safe_path:
        return "禁止访问", 403
    return send_from_directory(VIDEO_DIR, safe_path)


@app.route('/stream/<video_id>')
def stream_video(video_id):
    """稳定播放 URL：/stream/<id>，不暴露中文长文件名。"""
    return video_response_by_id(video_id)


@app.route('/thumb/<video_id>.jpg')
def thumb_video(video_id):
    """真实视频缩略图；首次访问自动生成并缓存。"""
    video = find_video(video_id)
    if not video:
        abort(404)
    path = generate_thumbnail(video)
    if not path:
        abort(404)
    return send_from_directory(THUMB_DIR, f'{video_id}.jpg', max_age=86400)


@app.route('/api/thumbs/generate')
def api_generate_thumbs():
    """手动批量生成缩略图：/api/thumbs/generate?limit=20&force=0"""
    limit = int(request.args.get('limit', '20'))
    force = request.args.get('force', '0') == '1'
    videos = get_video_files()[:limit]
    ok = 0
    failed = 0
    for v in videos:
        if generate_thumbnail(v, force=force):
            ok += 1
        else:
            failed += 1
    return jsonify({'ok': ok, 'failed': failed, 'limit': limit})


@app.route('/api/videos')
def api_videos():
    """API: 获取视频列表"""
    videos = get_video_files()
    folders = get_folders()
    
    folder = request.args.get('folder', '').strip()
    search = request.args.get('search', '').strip()
    view = request.args.get('view', 'home').strip() or 'home'
    filtered = filter_videos(videos, search=search, folder=folder, view=view)
    return jsonify({
        'videos': filtered,
        'folders': folders,
        'total': len(filtered),
        'queue': get_queue_summary(),
        'view': view,
    })

@app.route('/queue')
def queue_page():
    """下载队列状态页。"""
    items = get_queue_items()
    active_items = [x for x in items if x.get('status') in ('pending', 'downloading', 'retrying')]
    failed_items = [x for x in items if x.get('status') == 'failed']
    return render_template('queue.html', queue=get_queue_summary(), items=items,
                           active_items=active_items, failed_items=failed_items)


@app.route('/api/queue')
def api_queue():
    """队列状态 API。"""
    data = get_queue_summary()
    data['items'] = get_queue_items()
    return jsonify(data)


@app.route('/api/queue/control', methods=['POST'])
def api_queue_control():
    """网页控制 jdl 下载队列。仅执行固定白名单动作。"""
    action = (request.get_json(silent=True) or {}).get('action') if request.is_json else request.form.get('action')
    if action == 'start':
        result = jdl_command('daemon', 'start')
    elif action == 'stop':
        result = jdl_command('daemon', 'stop')
    elif action == 'retry_failed':
        result = jdl_command('retry-failed')
    elif action == 'run_once':
        result = jdl_command('daemon', 'start')
    elif action == 'clear_pending':
        conn = sqlite3.connect(db_path())
        cur = conn.execute("UPDATE download_queue SET status='skipped', error_msg='web skipped pending' WHERE status='pending'")
        conn.commit()
        count = cur.rowcount
        conn.close()
        result = {'ok': True, 'stdout': f'已跳过 pending: {count}', 'stderr': '', 'code': 0}
    elif action == 'clear_failed':
        conn = sqlite3.connect(db_path())
        cur = conn.execute("UPDATE download_queue SET status='skipped', error_msg='web skipped failed' WHERE status='failed'")
        conn.commit()
        count = cur.rowcount
        conn.close()
        result = {'ok': True, 'stdout': f'已跳过 failed: {count}', 'stderr': '', 'code': 0}
    else:
        result = {'ok': False, 'stdout': '', 'stderr': f'未知动作: {action}', 'code': 400}
    if request.headers.get('Accept', '').startswith('application/json') or request.is_json:
        payload = {'action': action, 'result': result, 'queue': get_queue_summary()}
        return jsonify(payload), (200 if result['ok'] else 400)
    return redirect(url_for('queue_page'))


@app.route('/player/<video_id>')
def player(video_id):
    """播放页面"""
    videos = get_video_files()
    video = next((v for v in videos if v['id'] == video_id), None)
    
    if not video:
        return "视频不存在", 404
    
    # 相关视频（同文件夹）
    related = [v for v in videos if v['folder'] == video['folder'] and v['id'] != video_id][:4]
    
    return render_template('player.html', 
                         video=video,
                         related=related)

@app.route('/health')
def health():
    """健康检查"""
    return jsonify({
        'status': 'ok',
        'service': 'simple-video',
        'port': PORT,
        'videos': len(get_video_files())
    })

if __name__ == '__main__':
    print("=" * 50)
    print("极简视频播放网站")
    print("=" * 50)
    print(f"视频目录: {VIDEO_DIR}")
    print(f"本地访问: http://localhost:{PORT}")
    print(f"内网访问: http://{HOST}:{PORT}")
    print(f"视频数量: {len(get_video_files())} 个")
    print("=" * 50)
    
    app.run(host=BIND_HOST, port=PORT, debug=False)