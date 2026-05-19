"""下载器模块 - m3u8实时获取+ffmpeg下载+队列管理"""
import os
import re
import time
import struct
import subprocess
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
import requests
from database import (get_setting, mark_downloaded, get_queue, update_queue_status,
                      update_download_progress, get_download_progress,
                      add_to_queue, get_conn, claim_next_queue_item,
                      recover_incomplete_downloads)
from crawler import fetch_video_detail, get_delay
from sites import get_adapter, detect_adapter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 全局下载队列
_download_queue = Queue()
_active_downloads = {}
_stop_event = threading.Event()


def log_event(message, video=None, error=None, extra=None):
    """写入下载器日志，保留失败上下文。"""
    from datetime import datetime
    logs_dir = os.path.join(BASE_DIR, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, 'jdl.log')
    code = ''
    if isinstance(video, dict):
        code = video.get('code') or str(video.get('id', ''))
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}"
    if code:
        line += f" | video={code}"
    if error:
        line += f" | error={error}"
    if extra:
        line += f" | extra={extra}"
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def verify_media_file(path):
    """用 ffprobe 校验输出文件是否是可解析媒体。"""
    if not os.path.exists(path) or os.path.getsize(path) <= 0:
        return False, '文件不存在或为空'
    try:
        proc = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1', path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return False, 'ffprobe校验失败: ' + (proc.stderr or proc.stdout or '').strip()
        return True, ''
    except Exception as e:
        return False, 'ffprobe异常: ' + str(e)


def sanitize_filename(name):
    """清理文件名，移除ffmpeg不支持的字符"""
    import unicodedata
    # 替换特殊Unicode符号为普通字符
    name = name.replace('●', '·').replace('・', '·')
    name = name.replace('「', '[').replace('」', ']')
    name = name.replace('！', '!').replace('？', '?')
    name = name.replace('（', '(').replace('）', ')')
    name = name.replace('、', ',')
    # 只保留安全字符
    safe = []
    for c in name:
        if c.isalnum() or c in ' _-.,()[]!@#$%^&+=\'~`':
            safe.append(c)
        elif ord(c) > 127 and not unicodedata.category(c).startswith('C'):
            # 保留合法Unicode（中文/日文/韩文等）
            safe.append(c)
    name = ''.join(safe)
    name = name.strip()[:80]
    return name if name else 'untitled'


def get_download_path(video, tag=''):
    """生成下载路径；多站点时把非 Jable 来源放进独立目录并给文件名加前缀。"""
    base_dir = get_setting('download_dir')
    site = video.get('site') or 'jable'
    if site != 'jable':
        base_dir = os.path.join(base_dir, sanitize_filename(site))
    if get_setting('auto_organize') == '1' and tag:
        base_dir = os.path.join(base_dir, sanitize_filename(tag))
    os.makedirs(base_dir, exist_ok=True)

    code = video.get('code') or str(video.get('id', 'video'))
    prefix = '' if site == 'jable' else f'{site}_'
    filename = f"{prefix}{code}_{sanitize_filename(video['title'])}.mp4"
    return os.path.join(base_dir, filename)


def get_video_adapter(video):
    """根据 video.site 或 URL 取得站点适配器。"""
    site = video.get('site') or ''
    if site:
        try:
            return get_adapter(site)
        except Exception:
            pass
    try:
        return detect_adapter(video.get('url', ''))
    except Exception:
        return get_adapter('jable')


def fetch_detail_for_video(video):
    adapter = get_video_adapter(video)
    # Jable 保持调用本模块导入的 fetch_video_detail，便于旧测试/旧调用 monkeypatch。
    if adapter.site == 'jable':
        detail = fetch_video_detail(video['url'])
        detail.setdefault('site', 'jable')
        detail.setdefault('media_url', detail.get('m3u8_url', ''))
        detail.setdefault('direct_url', '')
        detail.setdefault('is_downloadable', bool(detail.get('m3u8_url')))
        return detail
    return adapter.fetch_video_detail(video['url'])


def download_video(video, progress_callback=None):
    """
    下载单个视频
    video: dict with url, code, title, id, tags
    progress_callback: func(percent, speed, eta)
    返回: (success, file_path, file_size, error_msg)
    """
    # 1. 获取m3u8链接
    if progress_callback:
        progress_callback(0, '获取链接...', '')

    detail = fetch_detail_for_video(video)
    if detail.get('is_downloadable') is False:
        return False, '', 0, detail.get('skip_reason') or '内容不可下载'
    if not detail.get('m3u8_url'):
        return False, '', 0, 'm3u8链接获取失败'

    m3u8_url = detail['m3u8_url']

    # 2. 确定保存路径
    tag = video.get('tags', '').split(',')[0].strip()
    output_path = get_download_path(video, tag)

    if os.path.exists(output_path):
        file_size = os.path.getsize(output_path)
        if file_size > 1024 * 1024:  # >1MB认为已下载
            return True, output_path, file_size, '已存在'

    # 3. ffmpeg下载（带站点 Referer）
    temp_path = output_path + '.downloading'
    headers = get_video_adapter(video).get_headers(video.get('url'))
    ff_headers = ''.join(f'{k}: {v}\r\n' for k, v in headers.items() if k.lower() in ('referer', 'origin'))
    cmd = [
        'ffmpeg', '-y',
        '-user_agent', headers.get('User-Agent', 'Mozilla/5.0'),
        '-headers', ff_headers,
        '-i', m3u8_url,
        '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
        '-f', 'mp4',
    ]

    # 限速
    speed_limit = get_setting('speed_limit')
    if speed_limit and speed_limit != '0':
        cmd.extend(['-maxrate', f'{speed_limit}M', '-bufsize', f'{int(speed_limit)*2}M'])

    cmd.append(temp_path)
    
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True
        )

        # 监控进度
        duration_total = 0
        for line in process.stderr:
            if _stop_event.is_set():
                process.kill()
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return False, '', 0, '用户取消'

            # 解析总时长
            dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+)', line)
            if dur_match:
                h, m, s = map(int, dur_match.groups())
                duration_total = h * 3600 + m * 60 + s

            # 解析当前进度
            time_match = re.search(r'time=(\d+):(\d+):(\d+)', line)
            speed_match = re.search(r'speed=\s*([\d.]+)x', line)
            size_match = re.search(r'size=\s*(\d+)kB', line)

            if time_match and duration_total > 0:
                h, m, s = map(int, time_match.groups())
                current = h * 3600 + m * 60 + s
                percent = min(99, int(current / duration_total * 100))
                speed = speed_match.group(1) + 'x' if speed_match else '?'
                size_mb = int(size_match.group(1)) // 1024 if size_match else 0

                if progress_callback:
                    remaining = (duration_total - current) / float(speed_match.group(1)) if speed_match else 0
                    eta = f"{int(remaining//60)}:{int(remaining%60):02d}" if remaining > 0 else '?'
                    progress_callback(percent, f"{speed} | {size_mb}MB", eta)

        process.wait()
        
        if process.returncode == 0 and os.path.exists(temp_path):
            os.rename(temp_path, output_path)
            file_size = os.path.getsize(output_path)
            if video.get('id'):
                mark_downloaded(video['id'], output_path, file_size)
            if progress_callback:
                progress_callback(100, '完成', '')
            return True, output_path, file_size, ''
        else:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False, '', 0, f'ffmpeg退出码: {process.returncode}'

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return False, '', 0, str(e)


# ─── 多线程高速下载器 ───

def parse_m3u8(m3u8_url, headers=None):
    """解析m3u8文件，返回(segments, key_url, iv, media_sequence)。

    注意：HLS分片不一定以.ts结尾，可能带token/query或无扩展名；
    AES-128未显式给IV时，默认IV应使用media sequence number。
    """
    if headers is None:
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://jable.tv/'}
    if m3u8_url.startswith('file://'):
        with open(m3u8_url[7:], 'r', encoding='utf-8') as f:
            text = f.read()
    else:
        # 91 CDN 经常慢/抖，给一次 connect+read 长 timeout 并自动重试3次
        last_err = None
        text = None
        for attempt in range(1, 4):
            try:
                resp = requests.get(m3u8_url, headers=headers, timeout=(15, 60))
                resp.raise_for_status()
                text = resp.text
                break
            except Exception as e:
                last_err = e
                if attempt < 3:
                    time.sleep(min(2 * attempt, 5))
        if text is None:
            raise last_err if last_err else RuntimeError('parse_m3u8: empty response')
    lines = text.strip().split('\n')

    segments = []
    key_url = None
    iv = None
    media_sequence = 0

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            try:
                media_sequence = int(line.split(':', 1)[1].strip())
            except ValueError:
                media_sequence = 0
        elif line.startswith('#EXT-X-KEY:'):
            # AES-128 key
            uri_match = re.search(r'URI="([^"]+)"', line)
            iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', line)
            if uri_match:
                rel_key = uri_match.group(1)
                key_url = urljoin(m3u8_url, rel_key)
            if iv_match:
                iv_hex = iv_match.group(1)
                # IV必须是16字节，不足左补0，过长取低16字节
                iv = bytes.fromhex(iv_hex.zfill(32)[-32:])
        elif not line.startswith('#'):
            # 分片URL可以是 seg.ts、seg.ts?token=xxx 或无扩展名URL
            if m3u8_url.startswith('file://'):
                segments.append('file://' + os.path.abspath(os.path.join(os.path.dirname(m3u8_url[7:]), line)))
            else:
                segments.append(urljoin(m3u8_url, line))

    return segments, key_url, iv, media_sequence


def download_direct_file(video, direct_url, progress_callback=None, output_path=None, headers=None, concurrency=None):
    """下载 mp4 等直链文件，优先使用 Range 多线程并发下载。

    每个分块独立落盘到 .parts 目录，支持断点续传；合并后 ffprobe 校验。
    如果服务器不支持 Range，再回退到单线程流式下载。
    """
    tag = video.get('tags', '').split(',')[0].strip()
    if output_path is None:
        output_path = get_download_path(video, tag)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if os.path.exists(output_path) and os.path.getsize(output_path) > 1024 * 1024:
        return True, output_path, os.path.getsize(output_path), '已存在'

    headers = dict(headers or get_video_adapter(video).get_headers(video.get('url')))
    concurrency = int(concurrency or get_setting('direct_concurrency') or get_setting('segment_concurrency') or 8)
    retry_limit = int(get_setting('segment_retries') or get_setting('max_retries') or 3)
    chunk_size = int(get_setting('direct_chunk_mb') or 8) * 1024 * 1024
    parts_dir = output_path + '.direct.parts'
    temp_path = output_path + '.direct.dlpart'

    def make_headers(extra=None):
        h = dict(headers)
        if extra:
            h.update(extra)
        return h

    try:
        if progress_callback:
            progress_callback(0, '探测直链...', '')

        probe = requests.get(direct_url, headers=make_headers({'Range': 'bytes=0-0'}), timeout=(15, 120), stream=True)
        range_supported = probe.status_code == 206
        content_range = probe.headers.get('Content-Range', '')
        total_size = 0
        m = re.search(r'/([0-9]+)$', content_range)
        if m:
            total_size = int(m.group(1))
        elif probe.headers.get('Content-Length'):
            total_size = int(probe.headers.get('Content-Length'))
        probe.close()

        if not range_supported or total_size <= 0:
            # 回退：单线程，但保留较长 read timeout，避免 91 CDN 慢速连接频繁失败。
            part_path = output_path + '.part'
            resume_pos = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            h = make_headers({'Range': f'bytes={resume_pos}-'} if resume_pos else None)
            if progress_callback:
                progress_callback(0, '直链单线程...', '')
            with requests.get(direct_url, headers=h, stream=True, timeout=(15, 120)) as resp:
                mode = 'ab' if resume_pos and resp.status_code == 206 else 'wb'
                if mode == 'wb':
                    resume_pos = 0
                resp.raise_for_status()
                total_header = int(resp.headers.get('Content-Length') or 0)
                total = resume_pos + total_header if total_header else 0
                downloaded = resume_pos
                start_time = time.time()
                last_update = start_time
                with open(part_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if _stop_event.is_set():
                            return False, '', 0, '用户取消'
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if progress_callback and now - last_update > 0.5:
                            pct = int(downloaded / total * 98) if total else 0
                            speed = downloaded / max(now - start_time, 0.001) / 1024 / 1024
                            eta = '?'
                            if total and speed > 0:
                                remain = (total - downloaded) / (speed * 1024 * 1024)
                                eta = f"{int(remain//60)}:{int(remain%60):02d}"
                            progress_callback(min(pct, 98), f'{speed:.1f}MB/s', eta)
                            last_update = now
            ok, verify_error = verify_media_file(part_path)
            if not ok:
                log_event('直链媒体校验失败', video=video, error=verify_error, extra=direct_url)
                return False, '', 0, verify_error
            os.replace(part_path, output_path)
            size = os.path.getsize(output_path)
            if video.get('id'):
                mark_downloaded(video['id'], output_path, size)
            if progress_callback:
                progress_callback(100, '完成', '')
            return True, output_path, size, ''

        os.makedirs(parts_dir, exist_ok=True)
        ranges = []
        start = 0
        idx = 0
        while start < total_size:
            end = min(start + chunk_size - 1, total_size - 1)
            ranges.append((idx, start, end))
            idx += 1
            start = end + 1

        if progress_callback:
            progress_callback(0, f'直链并发 {len(ranges)} 块', '')

        start_time = time.time()
        last_update = time.time()
        completed_lock = threading.Lock()

        def part_path(i):
            return os.path.join(parts_dir, f'{i:06d}.part')

        def part_ok(i, begin, end):
            p = part_path(i)
            return os.path.exists(p) and os.path.getsize(p) == (end - begin + 1)

        def download_part(i, begin, end):
            if part_ok(i, begin, end):
                return i, True, 'cached'
            last_error = ''
            h = make_headers({'Range': f'bytes={begin}-{end}'})
            for attempt in range(1, retry_limit + 1):
                try:
                    with requests.get(direct_url, headers=h, stream=True, timeout=(15, 120)) as resp:
                        if resp.status_code not in (200, 206):
                            resp.raise_for_status()
                        tmp = part_path(i) + '.tmp'
                        got = 0
                        with open(tmp, 'wb') as f:
                            for chunk in resp.iter_content(chunk_size=512 * 1024):
                                if _stop_event.is_set():
                                    return i, False, '用户取消'
                                if chunk:
                                    f.write(chunk)
                                    got += len(chunk)
                        expected = end - begin + 1
                        if got != expected:
                            raise IOError(f'分块大小不符 {got}/{expected}')
                        os.replace(tmp, part_path(i))
                        return i, True, ''
                except Exception as e:
                    last_error = str(e)
                    time.sleep(min(2 * attempt, 8))
            return i, False, last_error

        errors = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(download_part, i, begin, end): (i, begin, end)
                for i, begin, end in ranges
                if not part_ok(i, begin, end)
            }
            for future in as_completed(futures):
                i, ok, err = future.result()
                if not ok:
                    errors.append((i, err))
                now = time.time()
                if progress_callback and now - last_update > 0.5:
                    downloaded = sum(os.path.getsize(part_path(j)) for j, b, e in ranges if os.path.exists(part_path(j)))
                    pct = int(downloaded / total_size * 98)
                    speed = downloaded / max(now - start_time, 0.001) / 1024 / 1024
                    eta = '?'
                    if speed > 0:
                        remain = (total_size - downloaded) / (speed * 1024 * 1024)
                        eta = f"{int(remain//60)}:{int(remain%60):02d}"
                    progress_callback(min(pct, 98), f'{speed:.1f}MB/s', eta)
                    last_update = now

        missing = [(i, b, e) for i, b, e in ranges if not part_ok(i, b, e)]
        if missing:
            sample = ','.join(str(i) for i, _, _ in missing[:10])
            detail = '; '.join(f'{i}:{e}' for i, e in errors[:3])
            error_msg = f'直链分块未完整下载: 缺失{len(missing)}个, 索引{sample}, 错误:{detail}'
            log_event('直链分块下载失败', video=video, error=error_msg, extra=direct_url)
            return False, '', 0, error_msg

        if progress_callback:
            progress_callback(99, '合并直链分块...', '')
        with open(temp_path, 'wb') as out:
            for i, _, _ in ranges:
                with open(part_path(i), 'rb') as part:
                    import shutil as _sh
                    _sh.copyfileobj(part, out, length=4 * 1024 * 1024)

        ok, verify_error = verify_media_file(temp_path)
        if not ok:
            log_event('直链媒体校验失败', video=video, error=verify_error, extra=direct_url)
            return False, '', 0, verify_error
        os.replace(temp_path, output_path)
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)
        size = os.path.getsize(output_path)
        if video.get('id'):
            mark_downloaded(video['id'], output_path, size)
        if progress_callback:
            progress_callback(100, '完成', '')
        elapsed = time.time() - start_time
        avg_speed = size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        return True, output_path, size, f'{avg_speed:.1f}MB/s'
    except Exception as e:
        log_event('直链下载异常', video=video, error=str(e), extra=direct_url)
        return False, '', 0, str(e)


def fast_download_video(video, progress_callback=None,
                        concurrency=10, output_path=None):
    """
    多线程并发下载HLS视频，支持AES-128解密、分片落盘、断点续传和分片重试。
    concurrency: 单个视频的分片并发线程数。
    """
    if progress_callback:
        progress_callback(0, '获取链接...', '')

    detail = fetch_detail_for_video(video)
    if detail.get('is_downloadable') is False:
        return False, '', 0, detail.get('skip_reason') or '内容不可下载'
    direct_url = detail.get('direct_url', '')
    m3u8_url = detail.get('m3u8_url', '')
    if direct_url and not m3u8_url:
        return download_direct_file(video, direct_url, progress_callback, output_path,
                                    headers=get_video_adapter(video).get_headers(video.get('url')),
                                    concurrency=concurrency)
    if not m3u8_url:
        return False, '', 0, 'm3u8链接获取失败'

    headers = get_video_adapter(video).get_headers(video.get('url'))
    try:
        try:
            segments, key_url, iv, media_sequence = parse_m3u8(m3u8_url, headers=headers)
        except TypeError:
            segments, key_url, iv, media_sequence = parse_m3u8(m3u8_url)
    except Exception as e:
        return False, '', 0, f'解析m3u8失败: {e}'

    if not segments:
        return False, '', 0, '没有找到视频分段'

    total = len(segments)
    if progress_callback:
        progress_callback(0, f'{total}个分段', '')

    key_data = None
    if key_url:
        try:
            resp = requests.get(key_url, headers=headers, timeout=10)
            resp.raise_for_status()
            key_data = resp.content
        except Exception as e:
            return False, '', 0, f'获取解密key失败: {e}'

    tag = video.get('tags', '').split(',')[0].strip()
    if output_path is None:
        output_path = get_download_path(video, tag)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    parts_dir = output_path + '.parts'
    temp_ts_path = output_path + '.ts.dlpart'
    temp_mp4_path = output_path + '.mp4.dlpart'
    os.makedirs(parts_dir, exist_ok=True)

    session = requests.Session()
    session.headers.update(headers)
    # 连接池要 >= 并发数，否则会出现 "Connection pool is full" 后立刻 timeout
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        adapter = HTTPAdapter(
            pool_connections=max(concurrency, 10),
            pool_maxsize=max(concurrency * 2, 20),
            max_retries=Retry(total=2, backoff_factor=0.5,
                              status_forcelist=[429, 500, 502, 503, 504],
                              allowed_methods=frozenset(['GET', 'HEAD'])),
        )
        session.mount('https://', adapter)
        session.mount('http://', adapter)
    except Exception:
        pass  # 老版 urllib3 兜底，不致命

    start_time = time.time()
    completed_lock = threading.Lock()
    retry_limit = int(get_setting('segment_retries') or get_setting('max_retries') or 3)

    def part_path(idx):
        return os.path.join(parts_dir, f'{idx:06d}.ts')

    def decrypt_segment(idx, data):
        if not key_data:
            return data
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        seg_iv = iv if iv else (media_sequence + idx).to_bytes(16, byteorder='big')
        cipher = Cipher(algorithms.AES(key_data), modes.CBC(seg_iv))
        decryptor = cipher.decryptor()
        data = decryptor.update(data) + decryptor.finalize()
        pad_len = data[-1]
        if 0 < pad_len <= 16:
            data = data[:-pad_len]
        return data

    def download_seg(idx, url):
        target = part_path(idx)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            return idx, True, 'cached'

        last_error = ''
        for attempt in range(1, retry_limit + 1):
            try:
                if url.startswith('file://'):
                    with open(url[7:], 'rb') as f:
                        data = f.read()
                else:
                    resp = session.get(url, timeout=30)
                    resp.raise_for_status()
                    data = resp.content
                data = decrypt_segment(idx, data)
                tmp = target + '.tmp'
                with open(tmp, 'wb') as f:
                    f.write(data)
                os.replace(tmp, target)
                return idx, True, ''
            except Exception as e:
                last_error = str(e)
                time.sleep(min(2 * attempt, 8))
        return idx, False, last_error

    try:
        existing = sum(1 for i in range(total) if os.path.exists(part_path(i)) and os.path.getsize(part_path(i)) > 0)
        completed = existing
        errors = []
        last_update = time.time()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(download_seg, i, url): i
                for i, url in enumerate(segments)
                if not (os.path.exists(part_path(i)) and os.path.getsize(part_path(i)) > 0)
            }

            for future in as_completed(futures):
                idx, ok, err = future.result()
                if not ok:
                    errors.append((idx, err))
                else:
                    with completed_lock:
                        completed += 1

                now = time.time()
                if now - last_update > 0.5 and progress_callback:
                    pct = int(completed / total * 90)
                    elapsed = max(now - start_time, 0.001)
                    bytes_done = sum(os.path.getsize(part_path(i)) for i in range(total)
                                     if os.path.exists(part_path(i)))
                    speed = bytes_done / elapsed / 1024 / 1024
                    remaining = (total - completed) * elapsed / max(completed, 1)
                    eta = f"{int(remaining//60)}:{int(remaining%60):02d}" if remaining > 0 else '?'
                    progress_callback(pct, f'{speed:.1f}MB/s', eta)
                    last_update = now

        missing = [i for i in range(total) if not (os.path.exists(part_path(i)) and os.path.getsize(part_path(i)) > 0)]
        if missing:
            sample = ','.join(str(i) for i in missing[:10])
            detail = '; '.join(f'{i}:{e}' for i, e in errors[:3])
            error_msg = f'分片未完整下载: 缺失{len(missing)}个, 索引{sample}, 错误:{detail}'
            log_event('分片下载失败', video=video, error=error_msg, extra=m3u8_url)
            return False, '', 0, error_msg

        if progress_callback:
            progress_callback(92, '合并TS...', '')

        with open(temp_ts_path, 'wb') as out:
            for i in range(total):
                with open(part_path(i), 'rb') as part:
                    import shutil as _sh
                    _sh.copyfileobj(part, out, length=4 * 1024 * 1024)

        if progress_callback:
            progress_callback(96, '封装MP4...', '')

        cmd = [
            'ffmpeg', '-y',
            '-i', temp_ts_path,
            '-c', 'copy', '-bsf:a', 'aac_adtstoasc',
            '-movflags', '+faststart',
            '-f', 'mp4',
            temp_mp4_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(temp_mp4_path):
            err = (proc.stderr or proc.stdout or '').strip().split('\n')[-5:]
            error_msg = 'ffmpeg封装MP4失败: ' + ' | '.join(err)
            log_event('ffmpeg封装失败', video=video, error=error_msg, extra=m3u8_url)
            return False, '', 0, error_msg

        ok, verify_error = verify_media_file(temp_mp4_path)
        if not ok:
            log_event('媒体校验失败', video=video, error=verify_error, extra=m3u8_url)
            return False, '', 0, verify_error

        os.replace(temp_mp4_path, output_path)
        if os.path.exists(temp_ts_path):
            os.remove(temp_ts_path)
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)

        total_size = os.path.getsize(output_path)
        if video.get('id'):
            mark_downloaded(video['id'], output_path, total_size)
        if progress_callback:
            progress_callback(100, '完成', '')

        elapsed = time.time() - start_time
        avg_speed = total_size / elapsed / 1024 / 1024 if elapsed > 0 else 0
        return True, output_path, total_size, f'{avg_speed:.1f}MB/s'

    except Exception as e:
        for p in (temp_ts_path, temp_mp4_path):
            if os.path.exists(p):
                os.remove(p)
        log_event('下载异常', video=video, error=str(e), extra=m3u8_url)
        return False, '', 0, str(e)


class DownloadManager:
    """后台下载队列管理器"""

    def __init__(self):
        self.executor = None
        self.running = False
        self.current_downloads = {}  # {video_id: progress_info}
        self.lock = threading.Lock()

    def start(self, callback=None):
        """启动后台下载"""
        if self.running:
            return
        self.running = True
        _stop_event.clear()
        concurrency = int(get_setting('queue_workers') or get_setting('concurrency') or 1)
        self.executor = ThreadPoolExecutor(max_workers=concurrency)
        self._process_thread = threading.Thread(target=self._process_queue, args=(callback,), daemon=True)
        self._process_thread.start()

    def stop(self):
        """停止下载"""
        self.running = False
        _stop_event.set()
        if self.executor:
            self.executor.shutdown(wait=False)

    def _process_queue(self, callback=None):
        """处理下载队列"""
        while self.running:
            queue_items = get_queue(status='pending', limit=1)
            if not queue_items:
                time.sleep(2)
                continue

            item = queue_items[0]
            update_queue_status(item['id'], 'downloading')

            video = {
                'id': item['video_id'],
                'url': item['url'],
                'code': item['code'],
                'title': item['title'],
                'tags': item['tags'],
                'site': item.get('site') or 'jable',
            }

            def progress_cb(percent, speed, eta, vid=item['video_id']):
                with self.lock:
                    self.current_downloads[vid] = {
                        'percent': percent, 'speed': speed, 'eta': eta
                    }
                if callback:
                    callback(vid, percent, speed, eta)

            success, path, size, error = download_video(video, progress_cb)

            if success:
                update_queue_status(item['id'], 'done')
            else:
                retry = item['retry_count'] + 1
                max_retries = int(get_setting('max_retries') or 3)
                if retry >= max_retries:
                    update_queue_status(item['id'], 'failed', error)
                else:
                    conn = get_conn()
                    conn.execute("UPDATE download_queue SET status='pending', retry_count=? WHERE id=?",
                                 (retry, item['id']))
                    conn.commit()
                    conn.close()

            with self.lock:
                self.current_downloads.pop(item['video_id'], None)

            time.sleep(get_delay())

    def get_progress(self):
        """获取当前下载进度"""
        with self.lock:
            return dict(self.current_downloads)

    @property
    def is_running(self):
        return self.running


# 全局下载管理器实例
download_manager = DownloadManager()

# ─── 独立后台守护进程 ───

DAEMON_PID_FILE = '/tmp/jable_download_daemon.pid'


def is_daemon_running():
    """检查守护进程是否存活，并确认 PID 对应的是 jable downloader daemon。"""
    if not os.path.exists(DAEMON_PID_FILE):
        return False
    try:
        with open(DAEMON_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)

        cmdline_path = f'/proc/{pid}/cmdline'
        if os.path.exists(cmdline_path):
            with open(cmdline_path, 'r', encoding='utf-8', errors='ignore') as f:
                cmdline = f.read()
            if 'downloader.py' not in cmdline or '--daemon' not in cmdline:
                try:
                    os.remove(DAEMON_PID_FILE)
                except OSError:
                    pass
                return False
        return True
    except (OSError, ValueError):
        try:
            os.remove(DAEMON_PID_FILE)
        except OSError:
            pass
        return False


def start_background_daemon():
    """启动独立后台下载守护进程（子进程，不依赖jdl生命周期）"""
    if is_daemon_running():
        return True

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloader.py')
    # 直接用subprocess.Popen启动，脱离终端
    try:
        proc = subprocess.Popen(
            ['python3', script, '--daemon'],
            stdout=open('/tmp/jable_download_daemon.log', 'w'),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        time.sleep(1)
        return is_daemon_running()
    except Exception as e:
        print(f'启动守护进程失败: {e}')
        return False


def stop_background_daemon():
    """停止守护进程"""
    if not os.path.exists(DAEMON_PID_FILE):
        return
    try:
        with open(DAEMON_PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 15)  # SIGTERM
        os.remove(DAEMON_PID_FILE)
    except (OSError, ValueError):
        pass


def run_daemon_once():
    """处理一个下载队列项；返回 (item, success_status) 或 (None, None) 表示队列为空。

    success_status 取值: 'done' / 'failed' / 'skipped' / 'retrying'
    """
    item = claim_next_queue_item()
    if not item:
        return None, None

    update_download_progress(item['video_id'], 0, '', '', 'downloading')

    video = {
        'id': item['video_id'],
        'url': item['url'],
        'code': item['code'],
        'title': item['title'],
        'tags': item['tags'],
        'site': item.get('site') or 'jable',
    }

    def progress_cb(percent, speed, eta):
        update_download_progress(video['id'], percent, speed, eta, 'downloading')

    segment_concurrency = int(get_setting('segment_concurrency') or get_setting('concurrency') or 10)
    try:
        success, path, size, extra = fast_download_video(video, progress_cb, concurrency=segment_concurrency)
    except Exception as e:
        success, extra = False, str(e)

    if success:
        update_queue_status(item['id'], 'done')
        update_download_progress(video['id'], 100, '完成', '', 'done')
        return item, 'done'
    if '跳过' in str(extra) or '不可下载' in str(extra) or 'data-src' in str(extra):
        update_queue_status(item['id'], 'skipped', str(extra))
        update_download_progress(video['id'], 0, '', str(extra), 'skipped')
        return item, 'skipped'
    retry = item['retry_count'] + 1
    max_retries = int(get_setting('max_retries') or 3)
    if retry >= max_retries:
        update_queue_status(item['id'], 'failed', str(extra))
        update_download_progress(video['id'], 0, '', str(extra), 'failed')
        return item, 'failed'
    conn = get_conn()
    conn.execute("UPDATE download_queue SET status='pending', retry_count=?, error_msg=? WHERE id=?",
                 (retry, str(extra), item['id']))
    conn.commit()
    conn.close()
    update_download_progress(video['id'], 0, '', str(extra), 'retrying')
    return item, 'retrying'


def run_daemon():
    """守护进程入口：一直跑队列，直到收到停止信号。"""
    import signal as sig_module

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        running = False

    sig_module.signal(sig_module.SIGTERM, handle_signal)
    sig_module.signal(sig_module.SIGINT, handle_signal)

    with open(DAEMON_PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    print(f'[守护进程] 启动, PID={os.getpid()}', flush=True)
    recovered = recover_incomplete_downloads(
        reason='daemon startup recovered stale downloading task',
        consume_retry=False,  # 启动回收不算 retry，避免好任务被错杀
    )
    if recovered.get('pending') or recovered.get('failed'):
        print(f"[守护进程] 回收残留任务: pending={recovered['pending']} failed={recovered['failed']}", flush=True)

    def worker_loop(worker_id):
        while running:
            try:
                item, status = run_daemon_once()
                if item is None:
                    time.sleep(2)
                    continue
                code = item['code']
                if status == 'done':
                    print(f'[守护进程] worker-{worker_id} ✓ {code} 完成', flush=True)
                elif status == 'failed':
                    progress = get_download_progress(item['video_id']) or {}
                    print(f'[守护进程] worker-{worker_id} ✗ {code} failed: {progress.get("eta", "")[:120]}', flush=True)
                elif status == 'skipped':
                    progress = get_download_progress(item['video_id']) or {}
                    print(f'[守护进程] worker-{worker_id} ⊘ {code} skipped: {progress.get("eta", "")[:120]}', flush=True)
                elif status == 'retrying':
                    progress = get_download_progress(item['video_id']) or {}
                    print(f'[守护进程] worker-{worker_id} ↻ {code} 稍后重试: {progress.get("eta", "")[:120]}', flush=True)
                time.sleep(get_delay())
            except Exception as e:
                print(f'[守护进程] worker-{worker_id} 异常: {e}', flush=True)
                time.sleep(3)

    def watchdog_loop():
        """每 60s 扫一次 downloading/retrying 长时间无进度的任务，回收并 +1 retry。"""
        # 卡死阈值：5 分钟没有 progress 更新就视为僵尸
        stale_threshold = int(get_setting('watchdog_stale_seconds') or 300)
        while running:
            for _ in range(60):
                if not running:
                    return
                time.sleep(1)
            try:
                rec = recover_incomplete_downloads(
                    reason=f'watchdog: no progress for >{stale_threshold}s',
                    consume_retry=True,
                    stale_seconds=stale_threshold,
                )
                if rec.get('pending') or rec.get('failed'):
                    print(f"[守护进程] watchdog 回收僵尸任务: pending={rec['pending']} failed={rec['failed']}", flush=True)
            except Exception as e:
                print(f'[守护进程] watchdog 异常: {e}', flush=True)

    worker_count = max(1, int(get_setting('queue_workers') or get_setting('concurrency') or 1))
    threads = []
    for i in range(worker_count):
        t = threading.Thread(target=worker_loop, args=(i + 1,), daemon=True)
        t.start()
        threads.append(t)
    print(f'[守护进程] 队列并发 worker={worker_count}', flush=True)

    wt = threading.Thread(target=watchdog_loop, daemon=True)
    wt.start()
    print('[守护进程] watchdog 已启动 (扫描间隔 60s)', flush=True)

    while running:
        time.sleep(1)

    print(f'[守护进程] 停止', flush=True)
    recovered = recover_incomplete_downloads(
        reason='daemon stopped before task finished',
        consume_retry=False,  # 停 daemon 不算 retry
    )
    if recovered.get('pending') or recovered.get('failed'):
        print(f"[守护进程] 停止时回收残留任务: pending={recovered['pending']} failed={recovered['failed']}", flush=True)
    if os.path.exists(DAEMON_PID_FILE):
        os.remove(DAEMON_PID_FILE)


if __name__ == '__main__':
    import sys
    if '--daemon' in sys.argv:
        run_daemon()
    elif '--stop' in sys.argv:
        stop_background_daemon()
    elif '--status' in sys.argv:
        if is_daemon_running():
            print('running')
        else:
            print('stopped')
