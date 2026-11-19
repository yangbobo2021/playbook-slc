# playbook-slc

本仓库交付 Codex 和 Claude Code 可用的 Skill，入口是根目录 SKILL.md。诊断与证据工具仅依赖 Python 3 标准库。CLI 操作使用本机 npm、SLC、Playbook 和 Node.js。

- 改动前读 SKILL.md 和对应 references/ 文档，保持工具与文档一致。
- references/upstream/ 是第三方原文，只通过 scripts/sync_references.py 从固定 Git 提交刷新，保留许可和 manifest；不能直接编辑。
- 未覆盖的实现按固定来源查证，不凭空补齐。
- tests/fixtures/ 仅放自建合成样例。用户日志、会话、凭据和业务文件留在本地。
- 完成后运行 python3 -m unittest discover -s tests -v 和 python3 scripts/sync_references.py --verify。
- 新增源码/Markdown 使用 Apache-2.0、2026 Boboyang SPDX 头；第三方原样保留。
- Git 使用 Conventional Commits 和 AI Co-authored-by 尾注。
