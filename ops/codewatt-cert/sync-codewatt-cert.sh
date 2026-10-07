#!/usr/bin/env bash
# 职责：把 oci-arm 上 acme.sh 续期得到的 *.codewatt.cn 通配证书分发到所有承载 codewatt.cn 子域名的 OCI 机器，
#       并 reload nginx、校验对外实际提供的证书已更新。
# 部署位置：oci-arm 的 /home/ubuntu/sync-codewatt-cert.sh，作为 acme.sh 的 reloadcmd 被调用
#       （acme.sh --install-cert -d codewatt.cn --ecc --reloadcmd /home/ubuntu/sync-codewatt-cert.sh）。
# 关键依赖：arm -> base/amd 的免密 ssh（~/.ssh/id_ed25519）、三台机器 ubuntu 用户的免密 sudo、openssl、logger。
# 为什么需要它：证书只在 arm 上用阿里云 DNS 验证签发，base/amd 不跑 acme；2026-10 该脚本丢失导致 base/amd 证书过期。
set -euo pipefail

# acme.sh 从 cron 调用时 PATH 很短，显式设置避免找不到 ssh/openssl/systemctl
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly LOG_TAG="sync-codewatt-cert"
readonly SRC_DIR="/home/ubuntu/.acme.sh/codewatt.cn_ecc"
readonly DEST_DIR="/etc/ssl/codewatt.cn"
readonly SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=15)
readonly VERIFY_RETRIES=10
# 远端机器："ssh 目标|用于校验的 SNI 域名"
readonly REMOTE_HOSTS=(
  "ubuntu@159.54.175.64|codewatt.cn"
  "ubuntu@163.192.16.235|x.codewatt.cn"
)
# arm 本机用于校验的 SNI 域名
readonly LOCAL_SNI="paseo.codewatt.cn"

# log 同时写 syslog 和 stderr。
# 输入：级别(info/err)、英文消息。副作用：写 syslog。
# 注意：消息里禁止出现私钥等敏感内容。
log() {
  local level="$1" msg="$2"
  logger -t "$LOG_TAG" -p "user.${level}" -- "$msg"
  printf '%s [%s] %s\n' "$(date -u +%FT%TZ)" "$level" "$msg" >&2
}

# served_enddate 返回 host:443 在指定 SNI 下对外提供的证书 notAfter。
# 输入：主机地址、SNI 域名。输出：stdout 打印 notAfter 字符串；连接失败时输出为空。
served_enddate() {
  local addr="$1" sni="$2"
  echo | timeout 15 openssl s_client -connect "${addr}:443" -servername "$sni" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2 || true
}

# verify_served 校验目标机对外证书到期时间与源证书一致，不一致则返回 1。
# 输入：主机地址、SNI 域名、期望的 notAfter。
# 注意：systemctl reload 是异步的，新 worker 起来前旧 worker 仍用旧证书握手，所以要重试等待。
verify_served() {
  local addr="$1" sni="$2" want="$3" got="" i
  for (( i = 0; i < VERIFY_RETRIES; i++ )); do
    got="$(served_enddate "$addr" "$sni")"
    if [[ "$got" == "$want" ]]; then
      log info "verify ok host=${addr} sni=${sni} not_after='${got}'"
      return 0
    fi
    sleep 1
  done
  log err "verify failed host=${addr} sni=${sni} served_not_after='${got}' expected='${want}'"
  return 1
}

# sync_remote 把证书推到一台远端机器并 reload nginx。
# 输入：ssh 目标、SNI 域名、期望 notAfter。副作用：覆盖远端 /etc/ssl/codewatt.cn/ 下 4 个文件、reload 远端 nginx。
# 约束：先 nginx -t 再 reload，配置异常时不 reload，避免把正在服务的 nginx 打挂。
#       本函数在 if 条件里调用，set -e 不生效，因此每步都显式 || return 1。
sync_remote() {
  local target="$1" sni="$2" want="$3" tmp
  tmp="$(ssh "${SSH_OPTS[@]}" "$target" 'mktemp -d')" || return 1
  scp -q "${SSH_OPTS[@]}" \
    "$SRC_DIR/fullchain.cer" "$SRC_DIR/codewatt.cn.key" "$SRC_DIR/codewatt.cn.cer" "$SRC_DIR/ca.cer" \
    "${target}:${tmp}/" || return 1
  ssh "${SSH_OPTS[@]}" "$target" "set -euo pipefail
    trap 'rm -rf ${tmp}' EXIT
    sudo install -d -o root -g root -m 755 ${DEST_DIR}
    sudo install -o root -g root -m 644 ${tmp}/fullchain.cer ${DEST_DIR}/fullchain.pem
    sudo install -o root -g root -m 600 ${tmp}/codewatt.cn.key ${DEST_DIR}/privkey.pem
    sudo install -o root -g root -m 644 ${tmp}/codewatt.cn.cer ${DEST_DIR}/cert.pem
    sudo install -o root -g root -m 644 ${tmp}/ca.cer ${DEST_DIR}/ca.pem
    sudo nginx -t -q
    sudo systemctl reload nginx" || return 1
  verify_served "${target#*@}" "$sni" "$want"
}

# main 依次处理本机与所有远端；单台失败不阻断其余机器，但最终以非 0 退出让 acme.sh 记录失败。
main() {
  local want failed=0 entry
  want="$(openssl x509 -noout -enddate -in "$SRC_DIR/fullchain.cer" | cut -d= -f2)"
  log info "sync start source_not_after='${want}'"

  if sudo nginx -t -q && sudo systemctl reload nginx && verify_served 127.0.0.1 "$LOCAL_SNI" "$want"; then
    log info "local reload ok"
  else
    log err "local reload failed"
    failed=1
  fi

  for entry in "${REMOTE_HOSTS[@]}"; do
    if sync_remote "${entry%%|*}" "${entry#*|}" "$want"; then
      log info "remote sync ok target=${entry%%|*}"
    else
      log err "remote sync failed target=${entry%%|*}"
      failed=1
    fi
  done

  if (( failed )); then
    log err "sync finished with failures"
    exit 1
  fi
  log info "sync finished ok"
}

main "$@"
