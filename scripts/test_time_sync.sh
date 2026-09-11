#!/usr/bin/env bash
#
# test_time_sync.sh — 出力制御装置 時計同期確認試験（JETGR0004-1-2.1 / 2.1.15）検証スクリプト
#
# 職責:
#   装置時刻を意図的に（年月日を含めて）誤った値に設定し、NTP による時刻同期が
#   自動的に完了することと、その所要時間・同期後残差を客観的に実測・記録する。
#
# 同期先について:
#   出力制御機能付 PCS 等の時刻同期先は、スケジュールサーバ（電力サーバまたは配信
#   事業者サーバ）が提供する NTP サーバ機能である（東北電力ネットワーク 出力制御機能付
#   ＰＣＳ等 技術仕様書（高低圧）6.3 および 7.8）。実際の試験では第 1 引数に模擬
#   スケジュールサーバーのアドレスを指定する。既定値の ntp.nict.jp は日本標準時の
#   公開サーバで、スクリプト自体の動作確認用。
#
# 依存:
#   - bash, awk, date, logger, /proc/uptime
#   - 同期機構のいずれか: chrony(chronyc) / systemd-timesyncd / ntpdate / busybox ntpd
#   - 残差測定（任意・推奨）: python3 もしくは ntpdate -q
#
# 設計上の要点（なぜこうしているか）:
#   1) 所要時間は /proc/uptime（単調時間）で測る。date は試験中に大きく跳ぶため使えない。
#   2) 「同期完了」は被測ツールの終了コードではなく、システム時刻が実際に真値へ
#      跳んだ瞬間を高頻度ポーリングで検出して判定する。ツールの自己申告は信用しない。
#   3) 同期後の正確さは独立した読み取り専用 SNTP 問い合わせで offset を実測する。
#      これが判定基準ロの定量的根拠になる。
#   4) 被測対象以外の常駐 NTP デーモンは試験中停止する。放置すると「誰が時刻を
#      直したのか」が特定できず、測定値が無意味になる。
#   5) 失敗・中断時も EXIT トラップで必ず時刻を復旧する。復旧は試験開始前に記録した
#      真値基準 + 単調経過時間から算出する。
#
# 使い方:
#   sudo ./test_time_sync.sh [NTP_SERVER] ["FAKE_TIME"] [LIMIT_SECONDS]
#   例: sudo ./test_time_sync.sh 192.0.2.10 "2024-11-05 03:27:11" 60
#   NTP_SERVER    : 同期先（模擬スケジュールサーバー等）。既定 ntp.nict.jp
#   FAKE_TIME     : 誤設定する時刻。年月日も現在時刻と異なる値にすること
#   LIMIT_SECONDS : 技術仕様書に規定した同期所要時間の上限［秒］
#
# 環境変数:
#   FORCE_METHOD=auto|chrony|timesyncd|ntpdate|busybox   同期機構を強制指定
#   TOLERANCE_SEC=1.0        同期後に許容する残差（秒）
#   TIMEOUT_SECONDS=120      同期完了待ちの上限
#   LOG_TAG=...              syslog タグ
#   KEEP_FAKE_TIME=1         試験後に時刻を復旧しない（デバッグ用）
#
set -euo pipefail

SERVER="${1:-ntp.nict.jp}"
FAKE_TIME="${2:-2024-11-05 03:27:11}"
LIMIT_SECONDS="${3:-}"

FORCE_METHOD="${FORCE_METHOD:-auto}"
TOLERANCE_SEC="${TOLERANCE_SEC:-1.0}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-120}"
LOG_TAG="${LOG_TAG:-hulk-powerflow-limitation}"
KEEP_FAKE_TIME="${KEEP_FAKE_TIME:-0}"

TIMESYNCD_DROPIN="/run/systemd/system/systemd-timesyncd.service.d/zz-time-sync-test.conf"

# 状態（トラップとレポートで共有）
TRUE_EPOCH_BASE=""      # 試験開始時点の真値エポック（この時点の時刻は正しい前提）
MONO_BASE=""            # 上記に対応する単調時間
STOPPED_UNITS=""        # 試験のために停止した常駐ユニット（復旧対象）
SERVER_IP=""
METHOD=""
SYNC_OK=0
ELAPSED=""
OFFSET_BEFORE=""
OFFSET_AFTER=""
DELAY_AFTER=""
CLOCK_RESTORED="n/a"

# ---------------------------------------------------------------------------
# ログ / 異常終了
# ---------------------------------------------------------------------------

# log_msg: 標準出力と syslog の両方へ記録する。syslog 側が成績書の証跡になる。
log_msg() {
    local msg="$1"
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$msg"
    logger -p user.info -t "$LOG_TAG" -- "$msg" 2>/dev/null || true
}

# die: 異常終了。stdout は関数の戻り値として捕捉される場合があるため stderr へ出す。
die() {
    log_msg "[synchronize_time] ERROR: $1" >&2
    exit 1
}

mono_now() { awk '{print $1}' /proc/uptime; }

# ---------------------------------------------------------------------------
# 能力検出
# ---------------------------------------------------------------------------

HAVE_NSEC=0
HAVE_SUBSEC_SLEEP=0
HAVE_PY=0

# detect_capabilities: 環境差（組込み busybox 等）を先に確定させ、以降の分岐を単純化する。
# 副作用: HAVE_* を設定する。
detect_capabilities() {
    date '+%N' 2>/dev/null | grep -Eq '^[0-9]{9}$' && HAVE_NSEC=1 || HAVE_NSEC=0
    sleep 0.05 2>/dev/null && HAVE_SUBSEC_SLEEP=1 || HAVE_SUBSEC_SLEEP=0
    command -v python3 >/dev/null 2>&1 && HAVE_PY=1 || HAVE_PY=0
}

# now_epoch: 現在のシステム時刻をエポック秒（可能なら小数）で返す。
now_epoch() {
    if [ "$HAVE_NSEC" -eq 1 ]; then
        date '+%s.%N'
    else
        date '+%s'
    fi
}

# ---------------------------------------------------------------------------
# 読み取り専用 SNTP 問い合わせ（時刻は変更しない）
# ---------------------------------------------------------------------------

PY_SNTP='
import socket, struct, sys, time
ip = sys.argv[1]
best = None
for _ in range(4):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        t1 = time.time()
        s.sendto(b"\x1b" + 47 * b"\0", (ip, 123))
        pkt, _ = s.recvfrom(48)
        t4 = time.time()
        u = struct.unpack("!12I", pkt)
        def ts(i):
            return (u[i] - 2208988800) + u[i + 1] / 2.0 ** 32
        t2, t3 = ts(8), ts(10)          # receive / transmit timestamp
        offset = ((t2 - t1) + (t3 - t4)) / 2.0
        delay = (t4 - t1) - (t3 - t2)
        stratum = (u[0] >> 16) & 0xFF
        if best is None or delay < best[1]:
            best = (offset, delay, stratum)
    except Exception:
        pass
    finally:
        s.close()
if best is None:
    sys.exit(1)
print("%.6f %.6f %d" % best)
'

# sntp_probe: 指定 IP へ SNTP を投げ「offset delay stratum」を出力する。
# 入力: $1 = NTP サーバ IP
# 出力: 成功時 "offset delay stratum"（offset = server - local, 秒）。失敗時は空文字 + rc=1。
# 副作用: なし（システム時刻を変更しない）。測定の独立性を保つため必ず読み取り専用で使う。
sntp_probe() {
    local ip="$1" out=""
    if [ "$HAVE_PY" -eq 1 ]; then
        out="$(python3 -c "$PY_SNTP" "$ip" 2>/dev/null || true)"
    elif command -v ntpdate >/dev/null 2>&1; then
        # ntpdate -q は問い合わせのみで時刻を変更しない
        out="$(ntpdate -q -u "$ip" 2>/dev/null | awk '/offset/ {off=$(NF-1)} END {if (off != "") printf "%s 0 0", off}' || true)"
    fi
    [ -n "$out" ] || return 1
    printf '%s\n' "$out"
}

# resolve_reachable_server: 主機名の全 A レコードを順に SNTP 探査し、応答した最初の IP を採用する。
# 入力: $1 = ホスト名または IP
# 出力: 採用した IP。副作用: 到達性の検査結果を syslog に残す（不到達アドレスも証跡として残す）。
# 注意: 時刻サーバは複数 A レコードを持つことがあり、一部が無応答の実例がある
#       （re-ene-time.hepco.co.jp の 61.198.17.146 で実測）。先頭 1 件だけを見ると
#       到達可能なサーバがあるのに FAIL と誤判定する。
resolve_reachable_server() {
    local host="$1" addrs="" ip="" probe=""
    addrs="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u)"
    [ -n "$addrs" ] || die "Failed to resolve NTP server: $host"

    for ip in $addrs; do
        if probe="$(sntp_probe "$ip")"; then
            log_msg "[synchronize_time] NTP server reachable: $host -> $ip (offset/delay/stratum: $probe)" >&2
            printf '%s\n' "$ip"
            return 0
        fi
        log_msg "[synchronize_time] WARN: NTP server address not responding: $host -> $ip" >&2
    done
    die "No reachable NTP server address for $host (tried: $(echo $addrs | tr '\n' ' '))"
}

# ---------------------------------------------------------------------------
# 常駐 NTP デーモンの制御
# ---------------------------------------------------------------------------

# active_time_daemons: 現在稼働中の常駐 NTP ユニット名を列挙する。
active_time_daemons() {
    local u
    command -v systemctl >/dev/null 2>&1 || return 0
    for u in chrony chronyd systemd-timesyncd ntp ntpsec openntpd; do
        if systemctl is-active --quiet "$u" 2>/dev/null; then
            printf '%s\n' "$u"
        fi
    done
}

# stop_other_daemons: 被測対象($1)以外の常駐 NTP デーモンを停止する。
# 理由: 停止しないと「時刻を直したのは誰か」が特定できず、所要時間の測定値が無意味になる。
# 副作用: STOPPED_UNITS に記録し、EXIT トラップで復旧する。
stop_other_daemons() {
    local keep="$1" u
    for u in $(active_time_daemons); do
        [ "$u" = "$keep" ] && continue
        log_msg "[synchronize_time] Stopping resident NTP daemon for test isolation: $u"
        systemctl stop "$u" >/dev/null 2>&1 || true
        STOPPED_UNITS="$STOPPED_UNITS $u"
    done
}

# ---------------------------------------------------------------------------
# 時刻復旧（EXIT トラップ）
# ---------------------------------------------------------------------------

# cleanup: 成否・中断を問わず、装置を試験前の状態へ戻す。
# 復旧手順: 1) timesyncd の一時 drop-in を削除 2) 停止したデーモンを再起動
#           3) 時刻がまだ誤っていれば真値基準 + 単調経過時間から再設定 4) RTC 書き戻し
cleanup() {
    local rc=$?
    set +e

    if [ -f "$TIMESYNCD_DROPIN" ]; then
        rm -f "$TIMESYNCD_DROPIN"
        rmdir "$(dirname "$TIMESYNCD_DROPIN")" 2>/dev/null
        systemctl daemon-reload >/dev/null 2>&1
    fi

    if [ "$KEEP_FAKE_TIME" != "1" ] && [ -n "$TRUE_EPOCH_BASE" ] && [ -n "$MONO_BASE" ]; then
        local expected now drift
        expected="$(awk -v b="$TRUE_EPOCH_BASE" -v m="$(mono_now)" -v mb="$MONO_BASE" \
            'BEGIN {printf "%.0f", b + (m - mb)}')"
        now="$(date '+%s')"
        drift="$(awk -v a="$now" -v b="$expected" 'BEGIN {d = a - b; if (d < 0) d = -d; printf "%.0f", d}')"
        if [ "$drift" -gt 5 ]; then
            date -s "@$expected" >/dev/null 2>&1
            CLOCK_RESTORED="restored from monotonic baseline (drift was ${drift}s)"
            log_msg "[synchronize_time] Clock restored from monotonic baseline (was off by ${drift}s)"
        else
            CLOCK_RESTORED="already correct (drift ${drift}s)"
        fi
    fi

    for u in $STOPPED_UNITS; do
        log_msg "[synchronize_time] Restarting resident NTP daemon: $u"
        systemctl start "$u" >/dev/null 2>&1
    done

    command -v hwclock >/dev/null 2>&1 && hwclock -w 2>/dev/null

    exit $rc
}

# ---------------------------------------------------------------------------
# 同期完了の検出
# ---------------------------------------------------------------------------

# daemon_synced: 同期機構自身が「NTP と同期済み」と報告しているかを返す。
# 入力: $1 = method
# 理由: systemd-timesyncd は起動時にまず /var/lib/systemd/timesync/clock の mtime へ
#       時計を戻す（ローカルファイルからの復元であって NTP 同期ではない）。この跳びを
#       同期完了と誤認すると、NTP とは無関係な所要時間を成績書に載せてしまう。
daemon_synced() {
    case "$1" in
        timesyncd)
            [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ]
            ;;
        chrony)
            chronyc tracking 2>/dev/null \
                | grep -Eq 'Leap status[[:space:]]*:[[:space:]]*Normal' \
                && ! chronyc tracking 2>/dev/null \
                    | grep -Eq 'Reference ID[[:space:]]*:[[:space:]]*00000000'
            ;;
        *)
            return 0
            ;;
    esac
}

# wait_sync_complete: 同期が真に完了するまで待ち、完了時点の単調時間を返す。
# 入力: $1 = 待ち上限（秒）, $2 = method
# 出力: "完了時点の単調時間 最初に時計が動いた時点の単調時間"（後者は無ければ "-"）。
#       期限内に完了しなければ rc=1。
# 注意: 本関数はコマンド置換（サブシェル）で呼ばれるため、結果は変数代入ではなく
#       必ず stdout で返す。サブシェル内の代入は親シェルに伝わらない。
# 完了条件: (a) |システム時刻 - 真値| <= TOLERANCE_SEC かつ (b) daemon_synced
# 「最初に時計が動いた時点」を併せて返す理由: systemd-timesyncd はローカルの
# clock ファイルからの復元で先に時計を動かすため、NTP 同期と区別して記録する。
wait_sync_complete() {
    local limit="$1" method="$2" start now expected mono
    local FIRST_JUMP_UPTIME=""
    start="$(mono_now)"
    while :; do
        mono="$(mono_now)"
        now="$(now_epoch)"
        expected="$(awk -v b="$TRUE_EPOCH_BASE" -v m="$mono" -v mb="$MONO_BASE" \
            'BEGIN {printf "%.3f", b + (m - mb)}')"

        # 最初に時計が動いた時点（真値から 3600 秒以内に入った時点）を記録する
        if [ -z "$FIRST_JUMP_UPTIME" ] \
            && awk -v n="$now" -v e="$expected" \
                'BEGIN {d = n - e; if (d < 0) d = -d; exit !(d <= 3600)}'; then
            FIRST_JUMP_UPTIME="$mono"
        fi

        if awk -v n="$now" -v e="$expected" -v t="$TOLERANCE_SEC" \
            'BEGIN {d = n - e; if (d < 0) d = -d; exit !(d <= t)}' \
            && daemon_synced "$method"; then
            printf '%s %s\n' "$mono" "${FIRST_JUMP_UPTIME:--}"
            return 0
        fi

        if awk -v s="$start" -v e="$(mono_now)" -v l="$limit" 'BEGIN {exit !((e - s) >= l)}'; then
            printf '%s %s\n' "$(mono_now)" "${FIRST_JUMP_UPTIME:--}"
            return 1
        fi
        if [ "$HAVE_SUBSEC_SLEEP" -eq 1 ]; then sleep 0.05; else sleep 1; fi
    done
}

# ---------------------------------------------------------------------------
# 同期機構ごとの実行
# ---------------------------------------------------------------------------

# pick_method: 使用する同期機構を決定する。
# 方針: 装置が実運用で使っている常駐機構を優先する（試験対象は装置の実装であって
#       試験用に持ち込んだツールではない）。常駐機構が無い場合に one-shot ツールを使う。
pick_method() {
    if [ "$FORCE_METHOD" != "auto" ]; then
        printf '%s\n' "$FORCE_METHOD"
        return 0
    fi
    local running
    running="$(active_time_daemons | head -1)"
    case "$running" in
        chrony|chronyd) printf 'chrony\n'; return 0 ;;
        systemd-timesyncd) printf 'timesyncd\n'; return 0 ;;
    esac
    if command -v ntpdate >/dev/null 2>&1; then printf 'ntpdate\n'; return 0; fi
    if command -v busybox >/dev/null 2>&1 && busybox --list 2>/dev/null | grep -qx ntpd; then
        printf 'busybox\n'; return 0
    fi
    if command -v chronyc >/dev/null 2>&1; then printf 'chrony\n'; return 0; fi
    die "No supported time synchronization mechanism found (chrony / systemd-timesyncd / ntpdate / busybox ntpd)."
}

# trigger_sync: 選択した機構で同期を起動する。ブロッキングでも非ブロッキングでも良い
#               （完了判定は wait_sync_complete が行う）。
# 入力: $1 = method, $2 = サーバ IP
trigger_sync() {
    local method="$1" ip="$2"
    case "$method" in
        ntpdate)
            timeout "$TIMEOUT_SECONDS" ntpdate -u "$ip" || true
            ;;
        busybox)
            timeout "$TIMEOUT_SECONDS" busybox ntpd -n -q -p "$ip" || true
            ;;
        chrony)
            # chronyd 稼働中に外部から即時測定・ステップを要求する
            chronyc -a burst 4/4 >/dev/null 2>&1 || true
            chronyc -a makestep >/dev/null 2>&1 || true
            ;;
        timesyncd)
            # 一時 drop-in で試験対象サーバを指定し、再起動して同期を起こす。
            # /run 配下なので再起動後に残らない。
            mkdir -p "$(dirname "$TIMESYNCD_DROPIN")"
            printf '[Time]\nNTP=%s\nFallbackNTP=\n' "$ip" > "$TIMESYNCD_DROPIN"
            systemctl daemon-reload >/dev/null 2>&1
            systemctl restart systemd-timesyncd >/dev/null 2>&1 \
                || die "Failed to restart systemd-timesyncd"
            ;;
        *)
            die "Unknown method: $method"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || { echo "ERROR: Please run as root (sudo)." >&2; exit 1; }
[ -r /proc/uptime ] || { echo "ERROR: /proc/uptime unavailable; cannot measure monotonic time." >&2; exit 1; }

detect_capabilities

echo "============================================================"
echo " Time Synchronization Verification Test (JETGR0004-1-2.1 / 2.1.15)"
echo "============================================================"
echo "NTP server   : $SERVER"
echo "Fake time    : $FAKE_TIME"
echo "Tolerance    : ${TOLERANCE_SEC} s"
if [ -n "$LIMIT_SECONDS" ]; then
    echo "Limit        : ${LIMIT_SECONDS} s (spec value)"
else
    echo "Limit        : not set (measurement only)"
fi
echo "Capabilities : nsec=$HAVE_NSEC subsec_sleep=$HAVE_SUBSEC_SLEEP python3=$HAVE_PY"
echo
echo "WARNING: This test deliberately changes the system clock."
echo "============================================================"
echo

# 1. 到達可能な NTP サーバを確定する（時刻はまだ変更しない）
SERVER_IP="$(resolve_reachable_server "$SERVER")"

# 2. 真値基準を記録する。この時点のシステム時刻は正しい前提（復旧の拠り所になる）
TRUE_EPOCH_BASE="$(now_epoch)"
MONO_BASE="$(mono_now)"
trap cleanup EXIT INT TERM

# 3. 同期機構を決め、被測対象以外の常駐デーモンを止める
METHOD="$(pick_method)"
log_msg "[synchronize_time] Synchronization method under test: $METHOD"
# chrony は chronyd 稼働中に chronyc で操作するため残す。
# timesyncd は trigger_sync 側で起動して同期を起こすため、ここでは停止しておく。
# そうしないと誤設定直後に timesyncd が勝手に時刻を直し、計時開始前に同期が
# 終わってしまって所要時間 0 秒という無意味な結果になる。
case "$METHOD" in
    chrony) stop_other_daemons chrony ;;
    *)      stop_other_daemons "" ;;
esac

# 4. 装置時刻を意図的に（年月日を含めて）誤った値へ設定する
date -s "$FAKE_TIME" >/dev/null || die "Failed to set fake system time."
log_msg "[synchronize_time] Device time intentionally changed to $FAKE_TIME (year/month/day included)"

# 5. 誤設定が実際に効いていることを独立測定で裏付ける（計時開始前に実施）
if OFFSET_BEFORE="$(sntp_probe "$SERVER_IP" | awk '{print $1}')"; then
    log_msg "[synchronize_time] Offset before synchronization: ${OFFSET_BEFORE} s (device clock vs $SERVER_IP)"
else
    OFFSET_BEFORE=""
    log_msg "[synchronize_time] WARN: Offset before synchronization could not be measured independently"
fi

# 計時開始前に時刻が既に直っていたら、測定される所要時間は無意味になる。
# 黙って 0 秒を報告せず、試験手順の不備として明示的に失敗させる。
if [ -n "$OFFSET_BEFORE" ] \
    && awk -v o="$OFFSET_BEFORE" 'BEGIN {a = o; if (a < 0) a = -a; exit !(a < 60)}'; then
    die "Device clock was already corrected (offset ${OFFSET_BEFORE}s) before timing started; measurement would be invalid. Check that resident NTP daemons are stopped."
fi

# 6. 計時開始 → 同期起動 → 時刻が直った瞬間を検出 → 計時終了
START_UPTIME="$(mono_now)"
log_msg "[synchronize_time] Time synchronization started with $SERVER ($SERVER_IP)"

trigger_sync "$METHOD" "$SERVER_IP"

if WAIT_RESULT="$(wait_sync_complete "$TIMEOUT_SECONDS" "$METHOD")"; then
    SYNC_OK=1
else
    SYNC_OK=0
fi
DONE_UPTIME="$(printf '%s' "$WAIT_RESULT" | awk '{print $1}')"
FIRST_JUMP_UPTIME="$(printf '%s' "$WAIT_RESULT" | awk '{print $2}')"
[ "$FIRST_JUMP_UPTIME" = "-" ] && FIRST_JUMP_UPTIME=""
ELAPSED="$(awk -v s="$START_UPTIME" -v e="$DONE_UPTIME" 'BEGIN {printf "%.3f", e - s}')"

if [ -n "$FIRST_JUMP_UPTIME" ]; then
    FIRST_JUMP_SEC="$(awk -v s="$START_UPTIME" -v e="$FIRST_JUMP_UPTIME" 'BEGIN {printf "%.3f", e - s}')"
    log_msg "[synchronize_time] First clock movement detected at ${FIRST_JUMP_SEC} s; NTP-confirmed synchronization at ${ELAPSED} s"
else
    FIRST_JUMP_SEC=""
fi

# 7. 同期後の残差を独立測定する（判定基準ロの定量的根拠）
if probe_after="$(sntp_probe "$SERVER_IP")"; then
    OFFSET_AFTER="$(printf '%s' "$probe_after" | awk '{print $1}')"
    DELAY_AFTER="$(printf '%s' "$probe_after" | awk '{print $2}')"
    log_msg "[synchronize_time] Offset after synchronization: ${OFFSET_AFTER} s (round-trip delay ${DELAY_AFTER} s)"
    if ! awk -v o="$OFFSET_AFTER" -v t="$TOLERANCE_SEC" \
        'BEGIN {a = o; if (a < 0) a = -a; exit !(a <= t)}'; then
        log_msg "[synchronize_time] ERROR: Residual offset ${OFFSET_AFTER} s exceeds tolerance ${TOLERANCE_SEC} s"
        SYNC_OK=0
    fi
else
    OFFSET_AFTER=""
    log_msg "[synchronize_time] WARN: Offset after synchronization could not be measured independently"
fi

# 8. 結果出力
if [ "$SYNC_OK" -ne 1 ]; then
    log_msg "[synchronize_time] Time synchronization FAILED with $SERVER after ${ELAPSED} seconds (method: $METHOD)"
    echo
    echo "RESULT: FAIL"
    echo "Method       : $METHOD"
    echo "Elapsed      : ${ELAPSED} s"
    echo "Offset after : ${OFFSET_AFTER:-unmeasured} s"
    exit 3
fi

log_msg "[synchronize_time] Time synchronized successfully with $SERVER ($SERVER_IP)"
log_msg "[synchronize_time] Synchronization time: ${ELAPSED} seconds"
log_msg "[synchronize_time] Current device time: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log_msg "[synchronize_time] Synchronization method: $METHOD"

echo
echo "============================================================"
echo "RESULT: SYNC SUCCESS"
echo "Method          : $METHOD"
echo "NTP server      : $SERVER ($SERVER_IP)"
echo "Offset before   : ${OFFSET_BEFORE:-unmeasured} s"
echo "First movement  : ${FIRST_JUMP_SEC:-n/a} s (local restore, not NTP)"
echo "Elapsed time    : ${ELAPSED} s (NTP-confirmed)"
echo "Offset after    : ${OFFSET_AFTER:-unmeasured} s (tolerance ${TOLERANCE_SEC} s)"
echo "Device time now : $(date '+%Y-%m-%d %H:%M:%S %Z')"

JUDGEMENT="measured"
if [ -n "$LIMIT_SECONDS" ]; then
    if awk -v e="$ELAPSED" -v l="$LIMIT_SECONDS" 'BEGIN {exit !(e <= l)}'; then
        JUDGEMENT="PASS"
        echo "Judgement       : PASS (<= ${LIMIT_SECONDS} s)"
        log_msg "[synchronize_time] Judgement: PASS (synchronization completed within ${LIMIT_SECONDS} seconds)"
    else
        JUDGEMENT="FAIL"
        echo "Judgement       : FAIL (> ${LIMIT_SECONDS} s)"
        log_msg "[synchronize_time] Judgement: FAIL (synchronization exceeded ${LIMIT_SECONDS} seconds)"
    fi
fi
echo "============================================================"

# 9. 構造化証跡（成績書へそのまま添付できる形）
printf '%s\n' "{\"test\":\"JETGR0004-1-2.1_2.1.15\",\"method\":\"$METHOD\",\"ntp_server\":\"$SERVER\",\"ntp_server_ip\":\"$SERVER_IP\",\"fake_time\":\"$FAKE_TIME\",\"offset_before_sec\":\"${OFFSET_BEFORE:-null}\",\"first_clock_movement_sec\":\"${FIRST_JUMP_SEC:-null}\",\"elapsed_sec\":$ELAPSED,\"offset_after_sec\":\"${OFFSET_AFTER:-null}\",\"tolerance_sec\":$TOLERANCE_SEC,\"limit_sec\":\"${LIMIT_SECONDS:-null}\",\"judgement\":\"$JUDGEMENT\"}"

echo
echo "Recent test logs:"
if command -v journalctl >/dev/null 2>&1; then
    journalctl -t "$LOG_TAG" -n 25 --no-pager 2>/dev/null || true
elif [ -f /var/log/syslog ]; then
    grep -F "$LOG_TAG" /var/log/syslog | tail -n 25 || true
elif [ -f /var/log/messages ]; then
    grep -F "$LOG_TAG" /var/log/messages | tail -n 25 || true
fi

[ "$JUDGEMENT" = "FAIL" ] && exit 4
exit 0
