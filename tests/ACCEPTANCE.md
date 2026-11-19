<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Boboyang -->
# 首版验收记录

日期：2026-11-19。范围：Skill 指令、标准库辅助工具、参考源码切面和历史失败的离线分析。没有启动新的 SLC 完整编译，没有调用测试 Agent，没有修改用户工作流或上游程序。

## 工具集成验收

执行 `python3 -m unittest discover -s tests -v`。9 项集成测试覆盖：

1. 备份源描述、FSM、审计记录；逐字节一致，缺失入口单独记录，依赖不拷贝，旧备份不可覆盖。
2. 拒绝项目外路径、递归备份目标和显式软链接输入，不改原文件。
3. 真实格式 JSONL 摘要保留 recorded/current 两种版本，捕获结构化检查，容忍末尾未写完一行；可执行入口与生成模块均不执行。
4. 仅有 link 报错时保留终态未知，不虚构编译通过。
5. 路由超时提供 environment 调查线索，不自动认定代码有缺陷。
6. 自动选择最新 attempt，也支持明确指定历史 attempt；不存在输入显式标记。
7. 当前定义已修改时，识别它与历史失败输入 SHA-256 不同。
8. 从固定 Git 提交复制，忽略脏工作区；复制后校验并能检测篡改。
9. 随仓库分发的全部参考文件通过 manifest 校验。

这些测试不调用 SLC，不依赖网络和第三方 Python 包，运行在秒级。

## 历史失败离线回放

使用项目现有 2026-11-18 原始 events.jsonl，通过 inspect_case.py 指定历史 attempt，再由当前宿主人工核对产物和版本化源码。私有摘要保存在工作仓库被忽略的 local-results/；公开仓库不包含用户原始审计或业务目录。

| 例子 | 读取到的事实 | 离线判断 | 未进行的操作 |
| --- | --- | --- | --- |
| TDD | gears2fsm 失败，4 次结构化检查均 unverified，原输入与当前输入匹配 | 检查器未知字段模拟字符串，FSM 要求非空数组，导致后续路径连锁未证明；需单独核实额外问题记录项 | 未重跑 33 分钟编译，未声称修改方案必然通过 |
| 文档评审写回 | link 失败，confirmed 同时含只读与写回；当前描述已与失败输入不同 | schema 3 仓库效果契约缺少未提交修改的正常完成；拆分结果并增加提交授权是行为调整 | 未把后续成功覆盖成原流程通过，未替用户业务提交 |
| 路由超时 | text2gears 失败，无检查器调用，多次 routing timeout；当前描述与原输入不同 | 环境/连接调查方向，未发生生成代码的实质检查 | 未修改流程去修网络，未重新请求模型 |

三次摘要收集分别约 0.063、0.048、0.038 秒；这是读取已有证据的耗时，不是 SLC 编译耗时。原始阶段耗时保留在本地 summary.json。辅助工具只给线索，根因判断来自后续源码核对，不把关键词匹配当证明。

核对位置：slc/src/verify-coverage.ts 的 synthesizedFieldValue/synthOutput；生成 FSM 的 readBehaviorSlices；playbook/slc/link.md 的 repositoryDisposition；playbook/src/xstate-runtime.ts 的 dispositionMatches；slc/src/check-report.ts 的环境和 unverified 分类。均可按 references/source-map.md 定位。

## 参考快照

158 份上游原文，约 4.75 MB：SLC 0.23.0 的 93 份文件与 Playbook 19.1.0 的 65 份文件。完整提交与逐文件 SHA-256 在 references/upstream/manifest.json。用 `python3 scripts/sync_references.py --verify` 离线核验。

Skill frontmatter 使用 skill-creator 的 quick_validate.py 校验通过。该校验器需要 PyYAML，仅用于开发校验；Skill 的三个辅助工具不依赖 PyYAML。

## 边界

本轮不是独立模型盲测，不证明所有错误都能自动修复。没有执行新编译或启动业务，因此不报告新的编译成功率、业务通过率或修复后运行保障。后续真实修复仍需保留备份、行为差异和实际复验记录，并遵守时间预算。
