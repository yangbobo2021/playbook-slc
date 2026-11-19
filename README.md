<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Boboyang -->
# playbook-slc

一个分析 SLC CLI 编译错误与 Playbook 启动错误的 Codex Skill。结合原始审计、生成产物以及随仓库分发的版本化源码切面，定位问题，建议或按要求调整自然语言流程、修补工作流，并记录复验依据。

## 使用

将本仓库克隆到个人 Skill 目录，或从个人 Skill 目录建立指向工作仓库的链接。例如仓库位于当前目录的 playbook-slc 时：

```sh
git clone https://github.com/yangbobo2021/playbook-slc.git
mkdir -p ~/.agents/skills
ln -s "$PWD/playbook-slc" ~/.agents/skills/playbook-slc
```

已有同名目录时先检查，不覆盖。重新打开 Codex 会话后可自然语言触发，也可显式调用：

> 使用 $playbook-slc 分析这次 SLC 编译失败。先说明根因和修改建议。

> 修复这个工作流，允许修改自然语言描述或简单修补生成代码，先备份，保留 TDD 的核心流程。

Skill 由当前宿主 Agent 执行分析。三个辅助工具只依赖 Python 3，不执行用户生成代码、不调用模型；真正复验才需要用户已有的 SLC 和 Agent 环境。没有编译日志时可仅分析粘贴的错误，但会明确证据缺口。

## 包含什么

- [SKILL.md](SKILL.md)：诊断、修改、备份、复验与结论规则。
- [源码索引](references/source-map.md)：按症状定位 SLC/Playbook 的原始代码和规约。
- [原文切面](references/upstream/manifest.json)：固定提交、版本、来源 URL 与每个文件 SHA-256；真实文件随仓库发布，不依赖外部链接或 Git 子模块。
- scripts/inspect_case.py：只读收集实际版本、阶段、结构化检查和错误线索。
- scripts/backup_case.py：修改前按路径备份并逐文件校验，不覆盖旧备份。
- scripts/sync_references.py：从指定 Git 提交更新或离线校验快照，不联网、不执行上游代码。

切面采样于 2026-11-19，包含当时远端 main 的 SLC 0.23.0 与 Playbook 19.1.0；完整提交见 manifest。诊断以实际执行版本为准，更新快照按[刷新说明](references/source-map.md#刷新参考快照)。

## 修复结果如何解释

分开记录原描述直接通过、修改描述后通过、手修产物后通过、仍失败/未验证。允许为尽量编译成功而改变流程，但必须说明行为差异并保留用户核心目标。未执行复验只称“待复验”；编译成功不等于启动成功或业务结果正确。Skill 不默认修改 SLC/Playbook 程序或关闭检查器。

完整编译可能很慢。本仓库验收使用离线测试和既有失败证据，不启动新的真实编译。实际复验须先指定假设、阶段、时间预算和日志位置；没有新信息时不反复重跑。

## 验收

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_references.py --verify
```

测试覆盖实际审计格式、末尾半行、缺失证据、版本区分、备份完整性、输出目录防覆盖、Git 固定提交复制和快照篡改检测。行为验收及限制见 [tests/ACCEPTANCE.md](tests/ACCEPTANCE.md)。辅助工具测试不等于已验证任意修复一定通过编译。

本仓库使用 Apache-2.0。第三方切面保持原文与原有版权；来源和许可见 [NOTICE](NOTICE) 及各上游 LICENSE。
