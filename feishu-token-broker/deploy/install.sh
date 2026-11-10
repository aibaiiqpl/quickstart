#!/usr/bin/env bash
# 在 Broker 服务器上以 root 执行：安装/升级 /opt/feishu 下的 Feishu Token Broker。
#
# 用法：sudo ./deploy/install.sh   （在已同步到服务器的 feishu-token-broker 目录内执行）
# 前置：/usr/local/bin/uv、rsync、python3.12 已安装。
# 幂等：可重复执行；不会覆盖已有的 feishu.env 和 state/ 下的授权状态。
# 失败即停：feishu.env 缺失时放置模板后退出，不会以空配置启动服务。
set -euo pipefail

readonly PREFIX=/opt/feishu
readonly SVC_USER=feishu
readonly UNIT=feishu-token-broker.service
readonly UV=/usr/local/bin/uv
readonly PYTHON=/usr/bin/python3.12
readonly HEALTH_URL=http://127.0.0.1:8789/health
readonly SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "must run as root"
[[ -x $UV ]] || die "uv not found at $UV"
[[ -x $PYTHON ]] || die "python not found at $PYTHON"
command -v rsync >/dev/null || die "rsync not installed"

if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
fi

install -d -o root -g root -m 0755 "$PREFIX" "$PREFIX/app" "$PREFIX/docs"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0700 "$PREFIX/state"

# 代码目录归 root 所有，服务进程只读；.venv 命中末尾的排除规则，--delete 不会删掉服务器上已建好的环境
rsync -a --delete --chown=root:root \
  --exclude='__pycache__/' \
  --include=/src/*** --include=/pyproject.toml --include=/uv.lock --include=/.python-version \
  --exclude='*' \
  "$SRC_DIR/" "$PREFIX/app/"
(cd "$PREFIX/app" && UV_PYTHON_DOWNLOADS=never "$UV" sync --frozen --no-dev --compile-bytecode --python "$PYTHON")
install -o root -g root -m 0644 "$SRC_DIR/docs/client-setup.md" "$PREFIX/docs/client-setup.md"

if [[ ! -f $PREFIX/feishu.env ]]; then
  install -o root -g "$SVC_USER" -m 0640 "$SRC_DIR/deploy/feishu.env.example" "$PREFIX/feishu.env"
  die "created $PREFIX/feishu.env from template; fill it in and re-run"
fi
chown root:"$SVC_USER" "$PREFIX/feishu.env"
chmod 0640 "$PREFIX/feishu.env"

install -o root -g root -m 0644 "$SRC_DIR/deploy/$UNIT" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable --quiet "$UNIT"
systemctl restart "$UNIT"

for _ in $(seq 1 20); do
  if curl --fail --silent --max-time 2 "$HEALTH_URL" >/dev/null; then
    echo "OK: $UNIT active, $(curl --silent "$HEALTH_URL")"
    exit 0
  fi
  sleep 0.5
done
journalctl -u "$UNIT" -n 30 --no-pager >&2
die "$UNIT did not become healthy"
