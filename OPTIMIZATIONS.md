# jdl 优化记录

更新时间：2026-05-11

## 第一阶段已完成

1. 高速下载支持分片落盘和断点续传
   - 下载中分片保存到 `目标文件.mp4.parts/000000.ts` 这种目录。
   - 重试时会跳过已经存在且非空的分片。
   - 全部分片完整后再合并 TS，并用 ffmpeg 封装成 MP4。
   - 成功后自动删除 `.parts` 和临时文件。

2. 分片失败自动重试
   - 新增设置项：`segment_retries`，默认 `3`。
   - 每个分片独立重试，避免一个临时超时导致整部视频失败。

3. CLI 下载默认使用高速下载
   - `jdl download <番号>` 已从旧版 `download_video()` 改为 `fast_download_video()`。

4. 前台队列下载使用高速下载
   - 菜单里的前台队列处理也改用 `fast_download_video()`。

5. 退出菜单不再默认杀后台下载
   - 如果后台 daemon 正在运行，退出时会询问是否停止。
   - 默认不停止，让后台继续下载。

6. 修复新增计数虚高
   - `database.add_video()` 现在只有真正插入新记录才返回 `True`。
   - 重复 URL 会返回 `False`。

## 第二阶段已完成

1. 后台下载进度写入 SQLite
   - 新增表：`download_progress`。
   - 后台 daemon 下载时会持续写入：`video_id / percent / speed / eta / status / updated_at`。
   - `jdl` 菜单查看队列时，可以跨进程读取真实后台进度。

2. 并发设置拆分
   - 新增：`queue_workers`，默认 `1`，表示队列并发任务数。
   - 新增：`segment_concurrency`，默认 `10`，表示单个视频的分片并发数。
   - 保留旧 `concurrency` 作为兼容设置。

3. 后台 daemon 使用 `segment_concurrency`
   - 后台下载不再固定使用 `fast_download_video()` 默认值。
   - 会读取 SQLite 设置：`segment_concurrency`。

4. 增强 m3u8 提取
   - 新增 `extract_m3u8_url()`。
   - 支持普通 m3u8 URL。
   - 支持 JS 转义形式，例如：`https:\/\/cdn.example\/video\/index.m3u8?...`。
   - 兼容 `hlsUrl/source/src/url` 等常见字段。

## 第三阶段已完成

1. BeautifulSoup 解析视频列表
   - `parse_video_list()` 现在优先使用 BeautifulSoup 解析。
   - 能适应 Jable 卡片结构轻微变化。
   - 保留原正则作为 fallback，避免兼容性倒退。

2. ffprobe 完整性校验
   - 新增 `verify_media_file()`。
   - MP4 封装完成后先用 `ffprobe` 校验。
   - 校验失败不会标记为下载成功。

3. daemon PID 防误判
   - `is_daemon_running()` 不再只判断 PID 是否存在。
   - 会检查 `/proc/<pid>/cmdline`，确认进程包含 `downloader.py --daemon`。
   - 避免 PID 被系统复用后误认为 daemon 仍在运行。

4. 日志落盘
   - 新增 `log_event()`。
   - 日志目录：`/root/jable-crawler/logs/`。
   - 日志文件：`/root/jable-crawler/logs/jdl.log`。
   - 下载异常、分片失败、ffmpeg 失败、媒体校验失败都会记录上下文。

## 第四阶段已完成

1. 新增交互式「工具箱」
   - 主菜单新增 `10. 工具箱`。
   - 所有第四阶段功能都已接入交互菜单，不需要记单独命令。

2. 工具箱：系统自检 doctor
   - 自检 ffmpeg、ffprobe、数据库、下载目录、BeautifulSoup。
   - 输出每项 OK/FAIL 和整体状态。

3. 工具箱：重试失败任务
   - 一键把失败队列重置为 pending。
   - 同时清空 retry_count 和 error_msg。

4. 工具箱：清理临时文件
   - 清理下载目录下的临时文件：`*.parts`、`*.dlpart`、`*.downloading`。
   - 如果 daemon 没运行，还会清理过期 PID 文件。

5. 工具箱：直接链接下载
   - 不需要先爬元数据，可以直接下载单个 Jable 视频链接。
   - 自动从 URL 提取番号作为文件名前缀。

6. 工具箱：后台下载管理
   - 查看状态
   - 启动后台
   - 停止后台
   - 查看日志

7. 同时保留命令行入口
   - `jdl doctor`
   - `jdl retry-failed`
   - `jdl clean`
   - `jdl download-url <链接>`
   - `jdl daemon start|stop|status|logs`

## 验证命令

```bash
cd /root/jable-crawler
python3 -m pytest -q test_jdl_optimizations.py
python3 -m py_compile jdl crawler.py downloader.py database.py
jdl doctor
jdl daemon status
jdl status
```

当前验证结果：

```text
19 passed
ffmpeg: OK
ffprobe: OK
database: OK
download_dir: OK
bs4: OK
overall: OK
daemon: running
总视频: 2172 | 已下载: 2 | 收藏: 0
```

## 常用命令

搜索：

```bash
jdl search SNOS
```

按番号下载：

```bash
jdl download SNOS-166
```

直接链接下载：

```bash
jdl download-url https://jable.tv/videos/xxxx/
```

自检：

```bash
jdl doctor
```

重试失败任务：

```bash
jdl retry-failed
```

清理临时文件：

```bash
jdl clean
```

后台管理：

```bash
jdl daemon status
jdl daemon start
jdl daemon stop
jdl daemon logs
```

调整下载参数：

```bash
jdl
# 进入 9. 设置
# queue_workers：队列并发数，建议 1
# segment_concurrency：单视频分片并发数，建议 8-16
# segment_retries：分片重试次数，建议 3-5
```

查看后台队列/进度：

```bash
jdl
# 进入 4. 批量下载 -> 4. 查看队列状态
```

查看日志：

```bash
tail -f /root/jable-crawler/logs/jdl.log
```

如果下载中断，不要删除 `.parts` 目录，重新下载同一个视频会自动续传。
