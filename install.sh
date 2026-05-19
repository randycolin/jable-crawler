#!/usr/bin/env bash
# JDL (Jable / 91PORNY Downloader) 一键安装脚本
# 适配：Debian / Ubuntu / Kali Linux
# 用法：
#   bash install.sh                 # 完整安装（含网页播放器）
#   bash install.sh --no-web        # 只装 CLI，不装网页播放器
#   bash install.sh --no-service    # 不创建 systemd 服务
#   PROJECT_DIR=/opt/jdl bash install.sh   # 自定义安装路径
set -Eeuo pipefail

# ───────────────────────────── 配置 ─────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
WEB_DIR="${WEB_DIR:-$PROJECT_DIR/web}"
VIDEO_DIR="${VIDEO_DIR:-$PROJECT_DIR/videos}"
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8090}"
JDL_BIN="${JDL_BIN:-/usr/local/bin/jdl}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

INSTALL_WEB=1
INSTALL_SERVICE=1
SKIP_APT=0

for arg in "$@"; do
  case "$arg" in
    --no-web)     INSTALL_WEB=0 ;;
    --no-service) INSTALL_SERVICE=0 ;;
    --skip-apt)   SKIP_APT=1 ;;
    -h|--help)
      grep -E '^#' "$0" | head -10
      exit 0 ;;
  esac
done

# ───────────────────────────── 日志 ─────────────────────────────
log()  { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }
hr()   { printf '\033[1;34m── %s ──\033[0m\n' "$*"; }
has_cmd() { command -v "$1" >/dev/null 2>&1; }

# ───────────────────────────── 检查 ─────────────────────────────
check_env() {
  hr "环境检查"
  if [[ "${EUID:-$(id -u)}" -ne 0 ]] && [[ "$INSTALL_SERVICE" -eq 1 ]]; then
    warn "未以 root 运行，--no-service 模式下可以继续，但 systemd 服务会跳过"
    INSTALL_SERVICE=0
  fi
  [[ -f "$PROJECT_DIR/jdl" ]] || { err "找不到 $PROJECT_DIR/jdl，请在仓库根目录执行 install.sh"; exit 1; }
  [[ -f "$PROJECT_DIR/requirements.txt" ]] || { err "缺少 requirements.txt"; exit 1; }
  log "项目目录：$PROJECT_DIR"
  log "网页播放器：$([[ $INSTALL_WEB -eq 1 ]] && echo 启用 || echo 跳过)"
  log "systemd 服务：$([[ $INSTALL_SERVICE -eq 1 ]] && echo 启用 || echo 跳过)"
}

# ───────────────────────────── 系统依赖 ─────────────────────────────
install_apt_deps() {
  [[ $SKIP_APT -eq 1 ]] && { warn "--skip-apt：跳过系统包安装"; return; }
  hr "安装系统依赖"
  if has_cmd apt-get; then
    log "apt: python3/venv/pip/ffmpeg/sqlite3/curl/build-essential"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      python3 python3-venv python3-pip python3-dev \
      ffmpeg sqlite3 curl ca-certificates \
      build-essential libssl-dev libffi-dev
  elif has_cmd dnf; then
    log "dnf: python3/python3-pip/ffmpeg/sqlite/curl/gcc/openssl-devel"
    dnf install -y python3 python3-pip python3-virtualenv ffmpeg sqlite curl gcc openssl-devel libffi-devel
  elif has_cmd pacman; then
    log "pacman: python/python-pip/ffmpeg/sqlite/curl/base-devel"
    pacman -Sy --noconfirm python python-pip python-virtualenv ffmpeg sqlite curl base-devel
  else
    warn "未识别的包管理器，请手动安装：python3 python3-venv ffmpeg sqlite3 curl"
  fi

  for c in python3 ffmpeg ffprobe sqlite3; do
    if has_cmd "$c"; then
      printf '  %-12s OK\n' "$c"
    else
      err "$c 未安装，请手动补齐"
      exit 1
    fi
  done
}

# ───────────────────────────── 目录 ─────────────────────────────
ensure_dirs() {
  hr "创建目录"
  mkdir -p "$VIDEO_DIR" "$PROJECT_DIR/logs"
  [[ $INSTALL_WEB -eq 1 ]] && mkdir -p "$WEB_DIR/static/thumbs"
  log "video=$VIDEO_DIR"
  log "logs=$PROJECT_DIR/logs"
}

# ───────────────────────────── Python 依赖 ─────────────────────────────
install_python_deps() {
  hr "安装 Python 依赖（CLI 端）"
  "$PYTHON_BIN" -m venv "$PROJECT_DIR/venv"
  "$PROJECT_DIR/venv/bin/python" -m pip install --upgrade pip wheel setuptools
  "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

  if [[ $INSTALL_WEB -eq 1 ]]; then
    hr "安装 Python 依赖（网页端）"
    "$PYTHON_BIN" -m venv "$WEB_DIR/venv"
    "$WEB_DIR/venv/bin/python" -m pip install --upgrade pip wheel setuptools
    if [[ -f "$WEB_DIR/requirements.txt" ]]; then
      "$WEB_DIR/venv/bin/pip" install -r "$WEB_DIR/requirements.txt"
    else
      "$WEB_DIR/venv/bin/pip" install 'flask>=3.0.0'
    fi
  fi
}

# ───────────────────────────── jdl CLI 包装器 ─────────────────────────────
install_jdl_command() {
  hr "安装 jdl 命令到 $JDL_BIN"
  chmod +x "$PROJECT_DIR/jdl"
  if [[ ! -w "$(dirname "$JDL_BIN")" ]]; then
    warn "$(dirname "$JDL_BIN") 不可写，跳过全局命令安装。可手动："
    warn "  alias jdl='$PROJECT_DIR/venv/bin/python $PROJECT_DIR/jdl'"
    return
  fi
  cat > "$JDL_BIN" <<EOF
#!/usr/bin/env bash
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/jdl" "\$@"
EOF
  chmod +x "$JDL_BIN"
  log "✓ jdl 命令已安装"
}

# ───────────────────────────── 初始化数据库 ─────────────────────────────
init_database() {
  hr "初始化 SQLite 数据库"
  cd "$PROJECT_DIR"
  "$PROJECT_DIR/venv/bin/python" - <<'PY'
import database
database.init_db()
print('✓ 数据库就绪：jable.db')
PY
}

# ───────────────────────────── systemd 服务 ─────────────────────────────
install_web_service() {
  if [[ $INSTALL_WEB -ne 1 ]] || [[ $INSTALL_SERVICE -ne 1 ]]; then
    return
  fi
  if ! has_cmd systemctl; then
    warn "systemctl 不可用，跳过服务安装；可手动启动：cd $WEB_DIR && venv/bin/python app.py"
    return
  fi

  hr "安装 simple-video.service"
  cat > /etc/systemd/system/simple-video.service <<EOF
[Unit]
Description=JDL Simple Video Player
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$WEB_DIR
Environment="PATH=$WEB_DIR/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="VIDEO_DIR=$VIDEO_DIR"
Environment="JDL_DIR=$PROJECT_DIR"
Environment="JDL_BIN=$JDL_BIN"
Environment="BIND_HOST=$WEB_HOST"
Environment="PORT=$WEB_PORT"
ExecStart=$WEB_DIR/venv/bin/python $WEB_DIR/app.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=simple-video

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable simple-video.service
  systemctl restart simple-video.service
  log "✓ 服务已启用并启动"
}

# ───────────────────────────── 验证 ─────────────────────────────
verify_install() {
  hr "验证安装"
  local fail=0
  for c in python3 ffmpeg ffprobe sqlite3 curl; do
    if has_cmd "$c"; then
      printf '  %-12s OK\n' "$c"
    else
      printf '  %-12s FAIL\n' "$c"
      fail=1
    fi
  done

  "$PROJECT_DIR/venv/bin/python" - <<'PY'
import importlib, sys
mods = ['requests', 'bs4', 'rich', 'cryptography', 'urllib3']
miss = []
for m in mods:
    try:
        importlib.import_module(m)
    except ImportError:
        miss.append(m)
if miss:
    print('✗ Python 依赖缺失:', miss); sys.exit(1)
print('✓ Python 依赖齐全')
PY

  if has_cmd "$JDL_BIN" || [[ -x "$JDL_BIN" ]]; then
    "$JDL_BIN" doctor 2>&1 | head -20 || true
  fi

  if [[ $INSTALL_WEB -eq 1 ]] && [[ $INSTALL_SERVICE -eq 1 ]] && has_cmd systemctl; then
    if systemctl is-active --quiet simple-video.service; then
      log "✓ simple-video.service 正在运行"
      sleep 2
      local code
      code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/health" || echo 000)"
      if [[ "$code" == "200" ]]; then
        log "✓ 网页 /health 返回 200：http://127.0.0.1:$WEB_PORT/"
      else
        warn "网页 /health 未返回 200（当前 $code），看日志：journalctl -u simple-video -n 50 --no-pager"
      fi
    else
      warn "simple-video.service 未运行：journalctl -u simple-video -n 50 --no-pager"
      fail=1
    fi
  fi

  return "$fail"
}

# ───────────────────────────── 主流程 ─────────────────────────────
main() {
  echo ""
  echo "╔════════════════════════════════════════╗"
  echo "║   JDL 安装脚本 v2.0                    ║"
  echo "║   Jable.tv / 91PORNY 视频管理 + 播放   ║"
  echo "╚════════════════════════════════════════╝"
  echo ""

  check_env
  install_apt_deps
  ensure_dirs
  install_python_deps
  install_jdl_command
  init_database
  install_web_service
  if verify_install; then
    log "🎉 安装完成"
  else
    warn "安装完成但部分检查未通过，详见上方输出"
  fi

  echo ""
  echo "─────────────────────────────────────────"
  echo "命令入口：  jdl"
  echo "项目路径：  $PROJECT_DIR"
  echo "视频目录：  $VIDEO_DIR"
  if [[ $INSTALL_WEB -eq 1 ]]; then
    echo "网页地址：  http://127.0.0.1:$WEB_PORT/"
    echo "局域网访问：http://<本机IP>:$WEB_PORT/"
  fi
  echo "─────────────────────────────────────────"
  echo ""
  echo "常用命令："
  echo "  jdl                 # 交互式菜单"
  echo "  jdl doctor          # 环境自检"
  echo "  jdl daemon start    # 启动下载守护"
  echo "  jdl daemon status   # 查看守护进程"
  echo "  jdl retry-failed    # 重试失败任务"
  echo "  jdl crawl <标签>    # 爬取分类"
  echo "  jdl download <番号> # 下载单个"
  echo ""
}

main "$@"
