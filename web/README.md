# 极简内网视频播放网站

## 特点
- **极简设计**：干净、简洁的界面，无杂乱元素
- **快速加载**：轻量级代码，快速响应
- **智能分类**：自动按文件夹分类视频
- **专业播放**：原生HTML5视频播放器
- **搜索功能**：支持视频名称搜索
- **播放进度保存**：自动保存播放进度

## 部署
```bash
# 启动服务
cd /root/video-simple
./start.sh

# 或直接运行
python3 app.py
```

## 访问地址
- 本地：http://localhost:8090
- 内网：http://<内网IP>:8090
- 健康检查：http://localhost:8090/health

## 技术栈
- 后端：Flask (Python)
- 前端：原生HTML/CSS/JavaScript
- 播放器：HTML5 Video
- 图标：Font Awesome

## 目录结构
```
/root/video-simple/
├── app.py              # 主应用
├── requirements.txt    # Python依赖
├── start.sh           # 启动脚本
├── simple-video.service # systemd服务
├── templates/         # 模板
│   ├── index.html    # 首页
│   └── player.html   # 播放页
└── static/           # 静态文件
    ├── css/
    │   └── style.css # 样式
    └── js/
        └── main.js   # JavaScript
```

## 功能说明
1. **侧边栏**：显示所有文件夹，点击筛选
2. **搜索框**：搜索视频名称
3. **视频网格**：显示视频缩略图和基本信息
4. **播放页面**：完整视频播放，相关视频推荐
5. **下载功能**：直接下载原始视频文件

## 维护
```bash
# 查看日志
journalctl -u simple-video -f

# 重启服务
systemctl restart simple-video

# 停止服务
systemctl stop simple-video
```

## 配置
- 视频目录：`/root/91down` (可在app.py中修改)
- 端口：8090 (可在app.py中修改)
- 支持格式：MP4, AVI, MKV, MOV, WebM