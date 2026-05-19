#!/bin/bash
# 极简视频网站启动脚本

echo "启动极简视频网站..."
echo "================================"

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 需要Python3"
    exit 1
fi

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "安装依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 获取IP
IP=$(hostname -I | awk '{print $1}')
if [ -z "$IP" ]; then
    IP="127.0.0.1"
fi

echo "================================"
echo "视频目录: /root/91down"
echo "本地访问: http://localhost:8090"
echo "内网访问: http://$IP:8090"
echo "================================"

# 启动
python3 app.py