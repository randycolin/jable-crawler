"""数据库模型 - SQLite存储元数据和进度"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'jable.db')


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT,
        title TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        tags TEXT DEFAULT '',
        thumbnail TEXT DEFAULT '',
        duration TEXT DEFAULT '',
        actress TEXT DEFAULT '',
        publish_date TEXT DEFAULT '',
        views INTEGER DEFAULT 0,
        favorited INTEGER DEFAULT 0,
        downloaded INTEGER DEFAULT 0,
        download_time TEXT DEFAULT '',
        file_path TEXT DEFAULT '',
        file_size INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        site TEXT DEFAULT 'jable',
        pay_status TEXT DEFAULT 'free',
        is_downloadable INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS crawl_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tag TEXT UNIQUE NOT NULL,
        tag_name TEXT DEFAULT '',
        last_page INTEGER DEFAULT 0,
        total_videos INTEGER DEFAULT 0,
        completed INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS download_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER NOT NULL,
        priority INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',
        retry_count INTEGER DEFAULT 0,
        error_msg TEXT DEFAULT '',
        added_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (video_id) REFERENCES videos(id)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS download_progress (
        video_id INTEGER PRIMARY KEY,
        percent INTEGER DEFAULT 0,
        speed TEXT DEFAULT '',
        eta TEXT DEFAULT '',
        status TEXT DEFAULT '',
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_videos_code ON videos(code);
    CREATE INDEX IF NOT EXISTS idx_videos_tags ON videos(tags);
    CREATE INDEX IF NOT EXISTS idx_videos_favorited ON videos(favorited);
    CREATE INDEX IF NOT EXISTS idx_videos_downloaded ON videos(downloaded);
    CREATE INDEX IF NOT EXISTS idx_queue_status ON download_queue(status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_queue_video_id ON download_queue(video_id);
    """)

    # 兼容旧数据库：SQLite CREATE TABLE IF NOT EXISTS 不会自动补新字段。
    for sql in [
        "ALTER TABLE videos ADD COLUMN site TEXT DEFAULT 'jable'",
        "ALTER TABLE videos ADD COLUMN pay_status TEXT DEFAULT 'free'",
        "ALTER TABLE videos ADD COLUMN is_downloadable INTEGER DEFAULT 1",
    ]:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e).lower():
                raise
    conn.execute("UPDATE videos SET site='jable' WHERE site IS NULL OR site=''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_site ON videos(site)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_pay_status ON videos(pay_status)")

    # 默认设置
    defaults = {
        'download_dir': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'videos'),
        'concurrency': '3',
        'request_delay': '2',
        'max_retries': '3',
        'speed_limit': '0',
        'proxy': '',
        'auto_organize': '1',
        'segment_retries': '3',
        'queue_workers': '1',
        'segment_concurrency': '10',
        'direct_concurrency': '8',
        'direct_chunk_mb': '8',
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()


def get_setting(key):
    conn = get_conn()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else None


def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()


def add_video(video_data):
    """添加视频，去重；只有真正插入新记录时返回 True。"""
    conn = get_conn()
    try:
        data = {
            'site': video_data.get('site', 'jable'),
            'code': video_data.get('code', ''),
            'title': video_data.get('title', ''),
            'url': video_data.get('url', ''),
            'tags': video_data.get('tags', ''),
            'thumbnail': video_data.get('thumbnail', ''),
            'duration': video_data.get('duration', ''),
            'actress': video_data.get('actress', ''),
            'publish_date': video_data.get('publish_date', ''),
            'pay_status': video_data.get('pay_status', 'free'),
            'is_downloadable': int(video_data.get('is_downloadable', 1)),
        }
        cur = conn.execute("""
            INSERT OR IGNORE INTO videos
                (site, code, title, url, tags, thumbnail, duration, actress, publish_date, pay_status, is_downloadable)
            VALUES
                (:site, :code, :title, :url, :tags, :thumbnail, :duration, :actress, :publish_date, :pay_status, :is_downloadable)
        """, data)
        conn.commit()
        return cur.rowcount > 0
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def search_videos(keyword='', tag='', sort='date', limit=50, offset=0, site=''):
    """搜索视频，支持模糊匹配和站点过滤。"""
    conn = get_conn()
    conditions = []
    params = []

    if keyword:
        conditions.append("(title LIKE ? OR code LIKE ? OR actress LIKE ? OR tags LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw])

    if tag:
        conditions.append("tags LIKE ?")
        params.append(f"%{tag}%")

    if site:
        conditions.append("site = ?")
        params.append(site)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    order_map = {
        'date': 'publish_date DESC',
        'title': 'title ASC',
        'duration': 'duration DESC',
        'views': 'views DESC',
        'newest': 'created_at DESC',
    }
    order = order_map.get(sort, 'created_at DESC')

    sql = f"SELECT * FROM videos {where} ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_videos(keyword='', tag='', site=''):
    conn = get_conn()
    conditions = []
    params = []
    if keyword:
        conditions.append("(title LIKE ? OR code LIKE ? OR actress LIKE ? OR tags LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw, kw])
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f"%{tag}%")
    if site:
        conditions.append("site = ?")
        params.append(site)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    row = conn.execute(f"SELECT COUNT(*) as cnt FROM videos {where}", params).fetchone()
    conn.close()
    return row['cnt']


def toggle_favorite(video_id):
    conn = get_conn()
    row = conn.execute("SELECT favorited FROM videos WHERE id=?", (video_id,)).fetchone()
    if row:
        new_val = 0 if row['favorited'] else 1
        conn.execute("UPDATE videos SET favorited=?, updated_at=datetime('now','localtime') WHERE id=?",
                     (new_val, video_id))
        conn.commit()
    conn.close()


def get_favorites(limit=50):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM videos WHERE favorited=1 ORDER BY updated_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_random_videos(count=5, tag=''):
    conn = get_conn()
    if tag:
        rows = conn.execute(
            "SELECT * FROM videos WHERE tags LIKE ? ORDER BY RANDOM() LIMIT ?",
            (f"%{tag}%", count)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM videos ORDER BY RANDOM() LIMIT ?", (count,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_to_queue(video_id, priority=0):
    """加入下载队列；同一video_id只保留一条队列记录。

    已失败/已完成的视频再次加入时，重置为pending，便于重新下载。
    """
    conn = get_conn()
    conn.execute("""
        INSERT INTO download_queue (video_id, priority, status, retry_count, error_msg)
        VALUES (?, ?, 'pending', 0, '')
        ON CONFLICT(video_id) DO UPDATE SET
            priority=excluded.priority,
            status='pending',
            retry_count=0,
            error_msg='',
            added_at=datetime('now','localtime')
    """, (video_id, priority))
    conn.commit()
    conn.close()


def get_queue(status='pending', limit=50):
    conn = get_conn()
    rows = conn.execute("""
        SELECT dq.*, v.code, v.title, v.url, v.tags, v.site, v.pay_status, v.is_downloadable
        FROM download_queue dq JOIN videos v ON dq.video_id = v.id
        WHERE dq.status = ? ORDER BY dq.priority DESC, dq.added_at ASC LIMIT ?
    """, (status, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def claim_next_queue_item():
    """原子领取一个待下载任务，避免多个队列 worker 抢到同一条。"""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT dq.*, v.code, v.title, v.url, v.tags, v.site, v.pay_status, v.is_downloadable
            FROM download_queue dq JOIN videos v ON dq.video_id = v.id
            WHERE dq.status = 'pending'
            ORDER BY dq.priority DESC, dq.added_at ASC
            LIMIT 1
        """).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute("UPDATE download_queue SET status='downloading', error_msg='' WHERE id=?", (row['id'],))
        conn.commit()
        item = dict(row)
        item['status'] = 'downloading'
        item['error_msg'] = ''
        return item
    finally:
        conn.close()


def recover_incomplete_downloads(max_retries=None, reason='daemon stopped before finishing',
                                 consume_retry=False, stale_seconds=None):
    """回收残留 downloading/retrying 任务。

    - consume_retry=False（默认，启动/停止 daemon 用）：纯环境问题，retry_count 不增加，
      避免好任务因 daemon 多次重启被错杀。
    - consume_retry=True（watchdog 用）：单条任务真卡死，retry_count +1，
      超过 max_retries 则标记 failed。
    - stale_seconds 不为 None 时，只回收 download_progress.updated_at 超时的任务，
      其他保持原状（避免误伤正在跑的）。

    返回 {'pending': n, 'failed': n}。
    """
    if max_retries is None:
        max_retries = int(get_setting('max_retries') or 3)
    conn = get_conn()
    pending = 0
    failed = 0
    try:
        if stale_seconds is not None:
            # watchdog 模式：只挑 progress 表里很久没更新的
            rows = conn.execute("""
                SELECT dq.id, dq.video_id, dq.retry_count
                FROM download_queue dq
                LEFT JOIN download_progress dp ON dp.video_id = dq.video_id
                WHERE dq.status IN ('downloading', 'retrying')
                  AND (dp.updated_at IS NULL
                       OR (julianday('now','localtime') - julianday(dp.updated_at)) * 86400 > ?)
            """, (int(stale_seconds),)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, video_id, retry_count FROM download_queue
                WHERE status IN ('downloading', 'retrying')
            """).fetchall()
        for row in rows:
            current_retry = int(row['retry_count'] or 0)
            retry = current_retry + 1 if consume_retry else current_retry
            if consume_retry and retry >= max_retries:
                conn.execute(
                    "UPDATE download_queue SET status='failed', retry_count=?, error_msg=? WHERE id=?",
                    (retry, reason, row['id'])
                )
                conn.execute("""
                    INSERT INTO download_progress (video_id, percent, speed, eta, status, updated_at)
                    VALUES (?, 0, '', ?, 'failed', datetime('now','localtime'))
                    ON CONFLICT(video_id) DO UPDATE SET
                        percent=0, speed='', eta=excluded.eta, status='failed', updated_at=datetime('now','localtime')
                """, (row['video_id'], reason))
                failed += 1
            else:
                conn.execute(
                    "UPDATE download_queue SET status='pending', retry_count=?, error_msg=? WHERE id=?",
                    (retry, reason, row['id'])
                )
                conn.execute("""
                    INSERT INTO download_progress (video_id, percent, speed, eta, status, updated_at)
                    VALUES (?, 0, '', ?, 'retrying', datetime('now','localtime'))
                    ON CONFLICT(video_id) DO UPDATE SET
                        percent=0, speed='', eta=excluded.eta, status='retrying', updated_at=datetime('now','localtime')
                """, (row['video_id'], reason))
                pending += 1
        conn.commit()
        return {'pending': pending, 'failed': failed}
    finally:
        conn.close()


def update_queue_status(queue_id, status, error_msg=''):
    conn = get_conn()
    conn.execute("UPDATE download_queue SET status=?, error_msg=? WHERE id=?",
                 (status, error_msg, queue_id))
    conn.commit()
    conn.close()


def update_download_progress(video_id, percent, speed='', eta='', status='downloading'):
    """保存跨进程可见的下载进度。"""
    conn = get_conn()
    conn.execute("""
        INSERT INTO download_progress (video_id, percent, speed, eta, status, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
        ON CONFLICT(video_id) DO UPDATE SET
            percent=excluded.percent,
            speed=excluded.speed,
            eta=excluded.eta,
            status=excluded.status,
            updated_at=datetime('now','localtime')
    """, (video_id, int(percent), str(speed), str(eta), str(status)))
    conn.commit()
    conn.close()


def get_download_progress(video_id=None):
    conn = get_conn()
    if video_id is None:
        rows = conn.execute("SELECT * FROM download_progress ORDER BY updated_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    row = conn.execute("SELECT * FROM download_progress WHERE video_id=?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_downloaded(video_id, file_path, file_size=0):
    conn = get_conn()
    conn.execute("""
        UPDATE videos SET downloaded=1, download_time=datetime('now','localtime'),
        file_path=?, file_size=?, updated_at=datetime('now','localtime') WHERE id=?
    """, (file_path, file_size, video_id))
    conn.commit()
    conn.close()


def get_stats():
    conn = get_conn()
    stats = {}
    stats['total_videos'] = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    stats['downloaded'] = conn.execute("SELECT COUNT(*) FROM videos WHERE downloaded=1").fetchone()[0]
    stats['favorited'] = conn.execute("SELECT COUNT(*) FROM videos WHERE favorited=1").fetchone()[0]
    stats['queue_pending'] = conn.execute("SELECT COUNT(*) FROM download_queue WHERE status='pending'").fetchone()[0]
    stats['queue_done'] = conn.execute("SELECT COUNT(*) FROM download_queue WHERE status='done'").fetchone()[0]
    stats['queue_skipped'] = conn.execute("SELECT COUNT(*) FROM download_queue WHERE status='skipped'").fetchone()[0]
    stats['total_size'] = conn.execute("SELECT COALESCE(SUM(file_size),0) FROM videos WHERE downloaded=1").fetchone()[0]
    stats['by_site'] = [dict(r) for r in conn.execute("SELECT site, COUNT(*) AS total FROM videos GROUP BY site ORDER BY total DESC").fetchall()]

    # 按标签统计
    rows = conn.execute("SELECT tags FROM videos").fetchall()
    tag_count = {}
    for r in rows:
        for t in r[0].split(','):
            t = t.strip()
            if t:
                tag_count[t] = tag_count.get(t, 0) + 1
    stats['top_tags'] = sorted(tag_count.items(), key=lambda x: -x[1])[:10]

    conn.close()
    return stats


def update_crawl_progress(tag, tag_name='', last_page=0, total_videos=0, completed=0):
    conn = get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO crawl_progress (tag, tag_name, last_page, total_videos, completed, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (tag, tag_name, last_page, total_videos, completed))
    conn.commit()
    conn.close()


def get_crawl_progress(tag=None):
    conn = get_conn()
    if tag:
        row = conn.execute("SELECT * FROM crawl_progress WHERE tag=?", (tag,)).fetchone()
        conn.close()
        return dict(row) if row else None
    else:
        rows = conn.execute("SELECT * FROM crawl_progress ORDER BY updated_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]


if __name__ == '__main__':
    init_db()
    print("数据库初始化完成")
