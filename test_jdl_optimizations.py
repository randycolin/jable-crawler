import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_jdl_module():
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("jdl_cli", str(ROOT / "jdl"))
    spec = importlib.util.spec_from_loader("jdl_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "jable-test.db"
    import database
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    database.init_db()
    return db_path


def test_add_video_returns_false_for_duplicate_url(temp_db):
    import database
    video = {
        "code": "ABC-001",
        "title": "title",
        "url": "https://jable.tv/videos/abc-001/",
        "tags": "tag",
        "thumbnail": "",
        "duration": "1:00",
        "actress": "",
        "publish_date": "",
    }

    assert database.add_video(video) is True
    assert database.add_video(video) is False


def test_cli_download_uses_fast_downloader(monkeypatch, temp_db, capsys):
    import database
    video = {
        "code": "ABC-002",
        "title": "title",
        "url": "https://jable.tv/videos/abc-002/",
        "tags": "tag",
        "thumbnail": "",
        "duration": "1:00",
        "actress": "",
        "publish_date": "",
    }
    database.add_video(video)

    jdl = load_jdl_module()
    called = {"fast": False, "old": False}

    def fake_fast(v, progress_callback=None):
        called["fast"] = True
        return True, "/tmp/out.mp4", 123, "ok"

    def fake_old(v, progress_callback=None):
        called["old"] = True
        return False, "", 0, "old should not be used"

    monkeypatch.setattr(jdl, "fast_download_video", fake_fast)
    monkeypatch.setattr(jdl, "download_video", fake_old)
    monkeypatch.setattr(sys, "argv", ["jdl", "download", "ABC-002"])

    assert jdl.cli_mode() is True
    assert called["fast"] is True
    assert called["old"] is False


def test_main_exit_does_not_stop_background_daemon(monkeypatch, temp_db):
    jdl = load_jdl_module()
    stopped = {"value": False}

    monkeypatch.setattr(jdl, "cli_mode", lambda: False)
    monkeypatch.setattr(jdl, "clear_screen", lambda: None)
    monkeypatch.setattr(jdl, "show_banner", lambda: None)
    monkeypatch.setattr(jdl, "show_main_menu", lambda: None)
    monkeypatch.setattr(jdl.Prompt, "ask", lambda *a, **k: "0")
    monkeypatch.setattr(jdl, "is_daemon_running", lambda: False)
    monkeypatch.setattr(jdl, "stop_background_daemon", lambda: stopped.__setitem__("value", True))

    jdl.main()
    assert stopped["value"] is False


def test_fast_download_reuses_existing_part_and_retries_failed_segment(monkeypatch, temp_db, tmp_path):
    import downloader

    video = {
        "id": 1,
        "code": "ABC-003",
        "title": "title",
        "url": "https://jable.tv/videos/abc-003/",
        "tags": "tag",
    }
    out = tmp_path / "out.mp4"
    parts_dir = Path(str(out) + ".parts")
    parts_dir.mkdir()
    (parts_dir / "000000.ts").write_bytes(b"existing")

    monkeypatch.setattr(downloader, "fetch_video_detail", lambda url: {"m3u8_url": "https://cdn.example/test.m3u8"})
    monkeypatch.setattr(downloader, "parse_m3u8", lambda url: (["https://cdn.example/0.ts", "https://cdn.example/1.ts"], None, None, 0))
    monkeypatch.setattr(downloader, "mark_downloaded", lambda *args, **kwargs: None)

    attempts = {"0": 0, "1": 0}

    class Resp:
        def __init__(self, content):
            self.content = content
        def raise_for_status(self):
            return None

    class FakeSession:
        headers = {}
        def get(self, url, timeout=30):
            if url.endswith("0.ts"):
                attempts["0"] += 1
                return Resp(b"should-not-redownload")
            attempts["1"] += 1
            if attempts["1"] == 1:
                raise RuntimeError("temporary fail")
            return Resp(b"new")

    monkeypatch.setattr(downloader.requests, "Session", lambda: FakeSession())

    def fake_run(cmd, stdout=None, stderr=None, universal_newlines=None, timeout=None):
        temp_ts = Path(cmd[cmd.index("-i") + 1])
        temp_mp4 = Path(cmd[-1])
        temp_mp4.write_bytes(temp_ts.read_bytes())
        class Proc:
            returncode = 0
            stderr = ""
            stdout = ""
        return Proc()

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    monkeypatch.setattr(downloader, "verify_media_file", lambda path: (True, ""))

    success, path, size, extra = downloader.fast_download_video(video, None, concurrency=2, output_path=str(out))

    assert success is True
    assert Path(path).read_bytes() == b"existingnew"
    assert attempts["0"] == 0
    assert attempts["1"] == 2
    assert not parts_dir.exists()


def test_download_progress_is_persisted_in_database(temp_db):
    import database
    database.update_download_progress(7, 42, "1.2MB/s", "00:10", "downloading")
    progress = database.get_download_progress(7)

    assert progress["video_id"] == 7
    assert progress["percent"] == 42
    assert progress["speed"] == "1.2MB/s"
    assert progress["eta"] == "00:10"
    assert progress["status"] == "downloading"


def test_defaults_split_queue_and_segment_concurrency(temp_db):
    import database
    assert database.get_setting("queue_workers") == "1"
    assert database.get_setting("segment_concurrency") == "10"


def test_recover_incomplete_downloads_requeues_stale_downloading(temp_db):
    import database
    video = {
        "code": "ABC-STALE", "title": "title", "url": "https://jable.tv/videos/abc-stale/",
        "tags": "tag", "thumbnail": "", "duration": "1:00", "actress": "", "publish_date": "",
    }
    database.add_video(video)
    row = database.search_videos(keyword="ABC-STALE", limit=1)[0]
    database.add_to_queue(row["id"])
    q = database.get_queue(status="pending", limit=1)[0]
    database.update_queue_status(q["id"], "downloading")

    result = database.recover_incomplete_downloads(max_retries=3, reason="test stale")
    assert result == {"pending": 1, "failed": 0}
    pending = database.get_queue(status="pending", limit=1)
    assert len(pending) == 1
    assert pending[0]["retry_count"] == 1
    assert database.get_download_progress(row["id"])["status"] == "retrying"


def test_claim_next_queue_item_is_atomic_and_marks_downloading(temp_db):
    import database
    for idx in range(2):
        video = {
            "code": f"ABC-CLAIM-{idx}", "title": "title", "url": f"https://jable.tv/videos/abc-claim-{idx}/",
            "tags": "tag", "thumbnail": "", "duration": "1:00", "actress": "", "publish_date": "",
        }
        database.add_video(video)
        row = database.search_videos(keyword=f"ABC-CLAIM-{idx}", limit=1)[0]
        database.add_to_queue(row["id"])

    first = database.claim_next_queue_item()
    second = database.claim_next_queue_item()
    assert first["id"] != second["id"]
    assert len(database.get_queue(status="downloading")) == 2
    assert len(database.get_queue(status="pending")) == 0


def test_daemon_uses_segment_concurrency_setting(monkeypatch, temp_db):
    import database
    import downloader

    video = {
        "code": "ABC-004",
        "title": "title",
        "url": "https://jable.tv/videos/abc-004/",
        "tags": "tag",
        "thumbnail": "",
        "duration": "1:00",
        "actress": "",
        "publish_date": "",
    }
    database.add_video(video)
    row = database.search_videos(keyword="ABC-004", limit=1)[0]
    database.add_to_queue(row["id"])
    database.set_setting("segment_concurrency", "17")

    called = {"concurrency": None}

    def fake_fast(video, progress_callback=None, concurrency=10, output_path=None):
        called["concurrency"] = concurrency
        return True, "/tmp/out.mp4", 123, "ok"

    monkeypatch.setattr(downloader, "fast_download_video", fake_fast)
    monkeypatch.setattr(downloader, "get_delay", lambda: 0)

    assert downloader.run_daemon_once() is True
    assert called["concurrency"] == 17


def test_fetch_video_detail_extracts_escaped_m3u8(monkeypatch, temp_db):
    import crawler

    class Resp:
        text = 'var hlsUrl = "https:\\/\\/cdn.example\\/video\\/index.m3u8?token=abc";'
        def raise_for_status(self):
            return None

    class FakeSession:
        def get(self, url, timeout=15):
            return Resp()

    monkeypatch.setattr(crawler, "get_session", lambda: FakeSession())
    detail = crawler.fetch_video_detail("https://jable.tv/videos/abc-005/")

    assert detail["m3u8_url"] == "https://cdn.example/video/index.m3u8?token=abc"


def test_parse_video_list_with_beautifulsoup_layout(temp_db):
    import crawler

    html = '''
    <div class="video-img-box">
      <a class="video-img-box" href="https://jable.tv/videos/abc-006/">
        <img data-src="https://img.example/abc-006.jpg">
        <span class="label">12:34</span>
      </a>
      <h6 class="title"><a>ABC-006 新版布局标题</a></h6>
    </div>
    '''

    videos = crawler.parse_video_list(html)
    assert len(videos) == 1
    assert videos[0]["code"] == "ABC-006"
    assert videos[0]["title"] == "ABC-006 新版布局标题"
    assert videos[0]["thumbnail"] == "https://img.example/abc-006.jpg"
    assert videos[0]["duration"] == "12:34"


def test_verify_mp4_with_ffprobe_rejects_invalid_file(monkeypatch, tmp_path):
    import downloader

    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a real mp4")

    class Proc:
        returncode = 1
        stderr = "invalid data"
        stdout = ""

    monkeypatch.setattr(downloader.subprocess, "run", lambda *a, **k: Proc())
    ok, error = downloader.verify_media_file(str(bad))
    assert ok is False
    assert "ffprobe" in error


def test_daemon_pid_check_rejects_unrelated_process(monkeypatch, tmp_path):
    import downloader

    pid_file = tmp_path / "daemon.pid"
    pid_file.write_text("12345")
    monkeypatch.setattr(downloader, "DAEMON_PID_FILE", str(pid_file))
    monkeypatch.setattr(downloader.os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(downloader.os.path, "exists", lambda path: True if str(path).endswith("daemon.pid") or str(path).endswith("/proc/12345/cmdline") else False)

    class FakeOpen:
        def __init__(self, path, *args, **kwargs):
            self.path = str(path)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            if self.path.endswith("cmdline"):
                return "python\x00other_script.py\x00"
            return "12345"
        def write(self, data):
            return len(data)

    monkeypatch.setattr("builtins.open", FakeOpen)
    assert downloader.is_daemon_running() is False


def test_log_event_writes_to_logs_directory(monkeypatch, tmp_path):
    import downloader

    monkeypatch.setattr(downloader, "BASE_DIR", str(tmp_path))
    downloader.log_event("测试日志", video={"code": "ABC-007"}, error="boom")

    log_path = tmp_path / "logs" / "jdl.log"
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "测试日志" in text
    assert "ABC-007" in text
    assert "boom" in text


def test_doctor_reports_core_dependencies(monkeypatch, temp_db):
    jdl = load_jdl_module()
    monkeypatch.setattr(jdl.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "ffprobe"} else None)
    result = jdl.run_doctor()
    assert result["ok"] is True
    assert result["checks"]["ffmpeg"] is True
    assert result["checks"]["ffprobe"] is True
    assert result["checks"]["database"] is True


def test_retry_failed_resets_failed_queue_items(temp_db):
    import database
    jdl = load_jdl_module()
    video = {
        "code": "ABC-008", "title": "title", "url": "https://jable.tv/videos/abc-008/",
        "tags": "tag", "thumbnail": "", "duration": "1:00", "actress": "", "publish_date": "",
    }
    database.add_video(video)
    row = database.search_videos(keyword="ABC-008", limit=1)[0]
    database.add_to_queue(row["id"])
    q = database.get_queue(status="pending", limit=1)[0]
    database.update_queue_status(q["id"], "failed", "boom")

    assert jdl.retry_failed() == 1
    assert len(database.get_queue(status="pending")) == 1
    assert len(database.get_queue(status="failed")) == 0


def test_clean_temp_files_removes_parts_dlpart_and_stale_pid(monkeypatch, tmp_path):
    jdl = load_jdl_module()
    (tmp_path / "a.mp4.parts").mkdir()
    (tmp_path / "b.ts.dlpart").write_text("x")
    (tmp_path / "c.mp4.downloading").write_text("x")
    pid = tmp_path / "daemon.pid"
    pid.write_text("99999")

    monkeypatch.setattr(jdl, "is_daemon_running", lambda: False)
    result = jdl.clean_temp_files(str(tmp_path), str(pid))

    assert result["removed"] >= 4
    assert not (tmp_path / "a.mp4.parts").exists()
    assert not (tmp_path / "b.ts.dlpart").exists()
    assert not pid.exists()


def test_download_url_builds_video_from_direct_url(monkeypatch, temp_db):
    jdl = load_jdl_module()
    captured = {}
    monkeypatch.setattr(jdl, "fetch_video_detail", lambda url: {"m3u8_url": "https://cdn.example/index.m3u8", "tags": "tag", "actress": ""})
    def fake_fast(video, progress_callback=None):
        captured.update(video)
        return True, "/tmp/out.mp4", 123, "ok"
    monkeypatch.setattr(jdl, "fast_download_video", fake_fast)

    success, path, size, extra = jdl.download_url("https://jable.tv/videos/abc-009/")
    assert success is True
    assert captured["code"] == "ABC-009"
    assert captured["url"] == "https://jable.tv/videos/abc-009/"


def test_daemon_cli_status_prints_state(monkeypatch, temp_db, capsys):
    jdl = load_jdl_module()
    monkeypatch.setattr(jdl, "is_daemon_running", lambda: True)
    monkeypatch.setattr(sys, "argv", ["jdl", "daemon", "status"])
    assert jdl.cli_mode() is True
    out = capsys.readouterr().out
    assert "running" in out


def test_main_menu_routes_to_tools(monkeypatch, temp_db):
    jdl = load_jdl_module()
    called = {"tools": False}
    answers = iter(["10", "0"])

    monkeypatch.setattr(jdl, "cli_mode", lambda: False)
    monkeypatch.setattr(jdl, "clear_screen", lambda: None)
    monkeypatch.setattr(jdl, "show_banner", lambda: None)
    monkeypatch.setattr(jdl, "show_main_menu", lambda: None)
    monkeypatch.setattr(jdl.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(jdl, "menu_tools", lambda: called.__setitem__("tools", True))
    monkeypatch.setattr(jdl, "is_daemon_running", lambda: False)

    jdl.main()
    assert called["tools"] is True


def test_menu_tools_doctor_option(monkeypatch, temp_db):
    jdl = load_jdl_module()
    called = {"doctor": False}
    answers = iter(["1", "0"])

    monkeypatch.setattr(jdl.Prompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(jdl, "print_doctor", lambda: called.__setitem__("doctor", True) or {"ok": True, "checks": {}})

    jdl.menu_tools()
    assert called["doctor"] is True


def test_porn91_batch_download_uses_keyword_not_jable_tag_mapping(monkeypatch, temp_db):
    import database
    jdl = load_jdl_module()
    video = {
        "site": "91porny", "code": "P91-BLACK", "title": "油亮开档黑丝", "url": "https://91porny.com/video/view/p91-black",
        "tags": "91porny,免费", "thumbnail": "", "duration": "00:01:00", "actress": "", "publish_date": "",
        "pay_status": "free", "is_downloadable": 1,
    }
    database.add_video(video)

    results = jdl.select_batch_videos("黑丝", site="91porny", limit=10)

    assert len(results) == 1
    assert results[0]["code"] == "P91-BLACK"


def test_porn91_search_and_download_direct_search_no_metadata_crawl(monkeypatch, temp_db):
    jdl = load_jdl_module()

    class FakeAdapter:
        def search_all_pages(self, keyword, max_pages=50):
            assert keyword == "鞋"
            return [{
                "site": "91porny",
                "code": "P91-ONE",
                "title": "search title",
                "url": "https://91porny.com/video/view/p91-one",
                "tags": "91porny,免费",
                "thumbnail": "",
                "duration": "00:01:00",
                "actress": "",
                "publish_date": "",
            }], {"pages_found": 1, "total_candidates": 1}
        def search(self, keyword, page):
            raise AssertionError("91 一步下载应先自动统计页数，不应要求手动输入页数后逐页调用")
        def is_m3u8_video(self, url):
            raise AssertionError("91 一步下载不应先逐条详情页探测/爬元数据")

    monkeypatch.setattr(jdl, "get_adapter", lambda name: FakeAdapter())
    monkeypatch.setattr(jdl.Prompt, "ask", lambda *a, **k: "鞋")
    monkeypatch.setattr(jdl.IntPrompt, "ask", lambda *a, **k: 1)
    monkeypatch.setattr(jdl.Confirm, "ask", lambda *a, **k: False)
    monkeypatch.setattr(jdl.time, "sleep", lambda *a, **k: None)

    downloaded = []
    def fake_fast(video, progress_callback=None, concurrency=10, output_path=None):
        downloaded.append(video["code"])
        return True, "/tmp/p91.mp4", 123, "ok"
    monkeypatch.setattr(jdl, "fast_download_video", fake_fast)

    result = jdl.porn91_search_and_download(keyword="鞋", pages=1, limit=1, background=False)

    assert result["pages_found"] == 1
    assert result["downloaded"] == 1
    assert result["failed"] == 0
    assert downloaded == ["P91-ONE"]


def test_porn91_search_all_pages_stops_on_late_http_error():
    from sites.porn91 import Porn91Adapter

    adapter = Porn91Adapter()

    def html_for(page):
        next_link = '<a href="/search?keywords=x&page=3">3</a>' if page == 1 else ''
        return f'''
        <div class="video-elem">
          <a class="title" href="/video/view/p{page}">title {page}</a>
        </div>
        {next_link}
        '''

    class Resp:
        def __init__(self, text='', fail=False):
            self.text = text
            self.fail = fail
        def raise_for_status(self):
            if self.fail:
                import requests
                raise requests.HTTPError("422 Client Error")

    class FakeSession:
        def get(self, url, timeout=30):
            if 'page=3' in url:
                return Resp(fail=True)
            if 'page=2' in url:
                return Resp(html_for(2))
            return Resp(html_for(1))

    adapter._session = lambda: FakeSession()

    videos, meta = adapter.search_all_pages("x")

    assert [v["code"] for v in videos] == ["p1", "p2"]
    assert meta["pages_found"] == 3
    assert meta["pages_fetched"] == 2
    assert meta["stopped_early"] is True


def test_porn91_search_and_download_skips_existing_downloaded_and_queued(monkeypatch, temp_db):
    import database
    jdl = load_jdl_module()

    existing_downloaded = {
        "site": "91porny", "code": "P91-DONE", "title": "done", "url": "https://91porny.com/video/view/done",
        "tags": "91porny,免费", "thumbnail": "", "duration": "00:01:00", "actress": "", "publish_date": "",
    }
    existing_queued = {
        "site": "91porny", "code": "P91-QUEUE", "title": "queued", "url": "https://91porny.com/video/view/queued",
        "tags": "91porny,免费", "thumbnail": "", "duration": "00:01:00", "actress": "", "publish_date": "",
    }
    database.add_video(existing_downloaded)
    database.add_video(existing_queued)
    done_row = database.search_videos(keyword="P91-DONE", site="91porny", limit=1)[0]
    queue_row = database.search_videos(keyword="P91-QUEUE", site="91porny", limit=1)[0]
    conn = database.get_conn()
    conn.execute("UPDATE videos SET downloaded=1, file_path=? WHERE id=?", ("/tmp/done.mp4", done_row["id"]))
    conn.commit()
    conn.close()
    database.add_to_queue(queue_row["id"])

    class FakeAdapter:
        def search_all_pages(self, keyword, max_pages=50):
            return [
                {"site": "91porny", "code": "P91-DONE", "title": "done", "url": "https://91porny.com/video/view/done", "tags": "91porny,免费"},
                {"site": "91porny", "code": "P91-QUEUE", "title": "queued", "url": "https://91porny.com/video/view/queued", "tags": "91porny,免费"},
                {"site": "91porny", "code": "P91-NEW", "title": "new", "url": "https://91porny.com/video/view/new", "tags": "91porny,免费"},
                {"site": "91porny", "code": "P91-NEW", "title": "new dup", "url": "https://91porny.com/video/view/new", "tags": "91porny,免费"},
            ], {"pages_found": 1, "total_candidates": 4}

    monkeypatch.setattr(jdl, "get_adapter", lambda name: FakeAdapter())
    monkeypatch.setattr(jdl.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(jdl, "is_daemon_running", lambda: True)

    result = jdl.porn91_search_and_download(keyword="鞋", pages=1, limit=10, background=True)

    assert result["duplicates"] == 1
    assert result["already_done"] == 1
    assert result["already_queued"] == 1
    assert result["queued"] == 1
    pending = database.get_queue(status="pending", limit=10)
    assert sorted(x["code"] for x in pending) == ["P91-NEW", "P91-QUEUE"]
