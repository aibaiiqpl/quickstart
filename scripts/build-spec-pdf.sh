#!/usr/bin/env bash
# 技術仕様書（日文提出用）の PDF を生成する。
#   入力: docs/spec-output-control-unit.md
#   出力: docs/spec-output-control-unit.pdf
# 依存: wkhtmltopdf（**patched qt 版**）。Ubuntu の apt 版は unpatched qt のため
#       --footer-* が無視され、ページ番号が入らない。patched 版が見つからない場合は
#       公式リリースの .deb を /tmp へ展開して使う（システムへは導入しない）。
set -euo pipefail

cd "$(dirname "$0")/.."
SRC=docs/spec-output-control-unit.md
OUT=docs/spec-output-control-unit.pdf
HTML=$(mktemp /tmp/spec-XXXXXX.html)
trap 'rm -f "$HTML"' EXIT

# patched qt 版の wkhtmltopdf を探す
find_wk() {
    local c
    for c in /tmp/wkx/usr/local/bin/wkhtmltopdf "$HOME/.local/bin/wkhtmltopdf" "$(command -v wkhtmltopdf || true)"; do
        [ -x "$c" ] || continue
        "$c" --version 2>/dev/null | grep -q 'patched qt' && { echo "$c"; return 0; }
    done
    return 1
}

if ! WK=$(find_wk); then
    echo "patched qt 版 wkhtmltopdf が無いため取得します" >&2
    # 社内プロキシは大容量ダウンロードが極端に遅いため外す
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
    curl -fsSL -o /tmp/wkhtmltox.deb \
        https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.jammy_amd64.deb
    dpkg-deb -x /tmp/wkhtmltox.deb /tmp/wkx
    WK=$(find_wk)
fi

python3 scripts/spec-md2html.py "$SRC" "$HTML"

"$WK" --quiet --enable-local-file-access --encoding utf-8 \
    --page-size A4 --margin-top 20mm --margin-bottom 20mm --margin-left 18mm --margin-right 18mm \
    --footer-font-name "IPAPGothic" --footer-font-size 7 --footer-spacing 7 --footer-line \
    --footer-left "出力制御装置技術仕様書  Ver. 1.00" \
    --footer-right "- [page] / [topage] -" \
    "$HTML" "$OUT"

echo "生成しました: $OUT ($(pdfinfo "$OUT" | awk '/^Pages/{print $2}') ページ)"
