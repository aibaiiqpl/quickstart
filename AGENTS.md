# 工作空间规则

## Agent 执行

- 严禁启动、调用或委派任何 sub-agent。设计、开发、调研、review、验收等所有工作必须由当前主 Agent 独立完成。
- 当 skill 或流程要求 sub-agent、fresh context、多 Agent 并行或独立 reviewer 时，不得执行这些调用；主 Agent 应在当前上下文中完成可执行步骤，并明确说明无法满足的独立上下文要求。
