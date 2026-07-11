#!/usr/bin/env bash
#
# init.sh — 初始化与 quickstart 同级的相关项目仓库
#
# 功能：在 quickstart 的上级目录（即与 quickstart 同级的位置）克隆一组项目仓库。
# 输入：无（仓库列表在脚本内 REPOS 集中定义）。
# 输出：每个仓库的处理结果日志；结尾打印汇总。
# 副作用：在上级目录创建/写入各仓库目录。
# 关键约束：
#   - 走 SSH（git@github.com），不受 HTTP 代理影响，无需 unset 代理。
#   - 幂等：目标目录已存在则仅提示并跳过，绝不覆盖或重复 clone。
#   - 逐个尝试、失败不中断，结尾统一报告；有失败则以非零码退出（fail-loud）。

set -euo pipefail

# 脚本所在目录（quickstart 内）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 目标目录：quickstart 的上级目录，各仓库将与 quickstart 平级
TARGET_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# 待克隆的仓库列表（集中定义，新增仓库只需在此追加）
REPOS=(
  "git@github.com:aibaiiqpl/emu-project.git"
  "git@github.com:aibaiiqpl/ems-project.git"
  "git@github.com:aibaiiqpl/smart-energy.git"
  "git@github.com:aibaiiqpl/TradingStrategy.git"
)

# repo_name_from_url — 从 git URL 提取仓库目录名
# 输入：git URL，如 git@github.com:org/repo.git
# 输出：去掉 .git 后缀的仓库名，如 repo
repo_name_from_url() {
  local url="$1"
  local base="${url##*/}"   # repo.git
  printf '%s' "${base%.git}"
}

main() {
  echo "目标目录（各仓库将克隆到此，与 quickstart 同级）：${TARGET_DIR}"
  echo

  local -a failed=()
  local -a skipped=()
  local -a cloned=()

  local url name dest
  for url in "${REPOS[@]}"; do
    name="$(repo_name_from_url "$url")"
    dest="${TARGET_DIR}/${name}"

    if [[ -e "${dest}" ]]; then
      if [[ -d "${dest}/.git" ]]; then
        echo "⏭  已存在（git 仓库），跳过：${name}"
      else
        echo "⚠  已存在但不是 git 仓库，跳过（避免覆盖）：${name}  (${dest})"
      fi
      skipped+=("${name}")
      continue
    fi

    echo "⬇  clone：${name}"
    if git clone "${url}" "${dest}"; then
      echo "✅ 完成：${name}"
      cloned+=("${name}")
    else
      echo "❌ 失败：${name}（保留错误上下文，见上方 git 输出）"
      failed+=("${name}")
    fi
    echo
  done

  echo "==== 汇总 ===="
  echo "新克隆 ${#cloned[@]} 个：${cloned[*]:-(无)}"
  echo "已跳过 ${#skipped[@]} 个：${skipped[*]:-(无)}"
  echo "失败   ${#failed[@]} 个：${failed[*]:-(无)}"

  if [[ ${#failed[@]} -gt 0 ]]; then
    return 1
  fi
}

main "$@"
