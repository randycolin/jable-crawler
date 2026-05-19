# JDL — Jable.tv / 91PORNY 视频管理与下载工具

视频站点的元数据爬取、批量下载、本地播放一体化方案。

> ⚠️ **仅供学习交流使用**。请遵守所在国家/地区法律法规与目标站点服务条款。仅下载你有合法访问权的内容。

## ✨ 功能

- **多站点适配**：内置 Jable.tv / 91PORNY 适配器，新站点只需写一个 `SiteAdapter` 子类
- **批量下载**：标签/分类/搜索结果整页拉，自动去重
- **HLS + 直链双路径**：自动识别 m3u8、AES-128 解密、TS 流式合并；mp4 直链支持分片并发
- **守护进程**：后台 worker 池 + 看门狗（5 分钟无进度自动回收僵尸任务）
- **网页播放器**（可选）：Flask 极简界面，本机 / 局域网访问，自动生成缩略图
- **交互式菜单 + CLI 双模**：日常用菜单、自动化用命令行

## 🚀 一键安装

```bash
git clone https://github.com/randycolin/jable-crawler.git
cd jable-crawler
sudo bash install.sh
```

### 安装选项

| 选项 | 说明 |
|------|------|
| `--no-web` | 不安装网页播放器（只装 CLI） |
| `--no-service` | 不创建 systemd 服务 |
| `--skip-apt` | 跳过 apt 系统包安装（已自己装好的话） |

### 环境变量覆盖

```bash
PROJECT_DIR=/opt/jdl  WEB_PORT=9000  sudo bash install.sh
```

## 📦 系统依赖

- Linux：Debian / Ubuntu / Kali / Arch / Fedora 均可
- Python 3.8+
- ffmpeg + ffprobe（处理 HLS 流必需）
- sqlite3、curl

## 🐍 Python 依赖（CLI 端）

```
requests, beautifulsoup4, rich, cryptography, urllib3, pytest
```

网页端额外需要 `flask`，安装脚本自动处理。

## 🎮 使用

### 交互菜单
```bash
jdl
```
按数字选菜单，包含：搜索 / 收藏 / 下载队列 / 维护 / 网页 / 守护进程管理。

### CLI 速查
```bash
jdl crawl <标签或番号>      # 爬取（中文标签自动转英文 slug）
jdl search <关键词>         # 本地数据库搜索
jdl download <番号>         # 按番号下载单个
jdl download-url <详情链接> # 按链接下载（自动识别站点）
jdl list [tag]              # 列出最近 20 条
jdl status                  # 总览统计
jdl doctor                  # 环境自检
jdl daemon start|stop|status # 守护进程
jdl retry-failed            # 重置失败任务
jdl clean                   # 清理临时文件
```

### 网页播放器
默认端口 `8090`：
- 首页：`http://127.0.0.1:8090/`
- 健康：`http://127.0.0.1:8090/health`
- 一键扫描视频目录、自动生成缩略图、HTML5 原生播放（支持 Range 请求）

## 🗂️ 项目结构

```
jable-crawler/
├── jdl                  # CLI 主入口
├── database.py          # SQLite 数据层
├── crawler.py           # Jable 爬虫
├── downloader.py        # 下载引擎（HLS / 直链 / 守护进程 / watchdog）
├── sites/               # 站点适配器
│   ├── base.py
│   ├── jable.py
│   └── porn91.py
├── web/                 # 网页播放器（Flask）
│   ├── app.py
│   ├── templates/
│   └── static/
├── install.sh           # 一键安装
└── requirements.txt
```

## ⚙️ 配置

所有配置存在 `jable.db` 的 `settings` 表里，通过菜单 → 设置 修改：

| key | 默认 | 说明 |
|-----|------|------|
| `download_dir` | `videos/` | 下载根目录 |
| `concurrency` | `8` | HLS 分片并发数 |
| `queue_workers` | `1` | 队列 worker 数 |
| `direct_concurrency` | `4` | 直链分片并发 |
| `request_delay` | `2` | 请求间隔基线（秒） |
| `proxy` | `''` | HTTP/HTTPS 代理（如 `http://127.0.0.1:7890`） |
| `watchdog_stale_seconds` | `300` | 卡死阈值，超时自动回收 |

## 🔌 添加新站点

在 `sites/` 下新建 `mysite.py`，继承 `SiteAdapter`：

```python
from .base import SiteAdapter

class MySiteAdapter(SiteAdapter):
    site = 'mysite'
    display_name = 'MySite'
    base_url = 'https://example.com'

    def matches_url(self, url):
        return 'example.com' in (url or '')

    def fetch_video_detail(self, url):
        # 返回字典：title / m3u8_url / direct_url / is_downloadable ...
        ...
```

然后在 `sites/__init__.py` 注册：
```python
from .mysite import MySiteAdapter
ADAPTERS = [JableAdapter(), Porn91Adapter(), MySiteAdapter()]
```

## 🛠️ 常见问题

**Q: 下载卡在某个百分比不动？**
A: watchdog 默认 5 分钟无进度回收。如想更激进，菜单 → 设置改 `watchdog_stale_seconds` 为 `120`。

**Q: 视频文件有了但播放报错？**
A: 跑 `jdl doctor`；如果 ffprobe 失败，可能是 TS 合并出错，删了重下。

**Q: 网页打不开？**
A: `systemctl status simple-video` 看日志；`journalctl -u simple-video -n 50`。

**Q: 想跑在非 root 用户下？**
A: `bash install.sh --no-service`，然后自己加 systemd user unit 或 supervisor。

## 📜 License

MIT — 个人学习用途。代码原作者保留署名权。

---

## ⚠️ 法律声明

本工具仅用于：
- 个人学习网络爬虫、HLS 协议、AES 解密等技术
- 备份自己有合法访问权的内容
- 局域网内的个人媒体库管理

**严禁**用于：
- 大规模商业爬取
- 传播他人版权作品
- 任何违法用途

使用本工具产生的一切后果，由使用者自行承担。
