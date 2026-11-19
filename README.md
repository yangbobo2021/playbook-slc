<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Boboyang -->
# playbook-slc

`playbook-slc` 是在本地 Codex 和 Claude Code 中使用的 Skill。它通过自然语言调用 SLC 和 Playbook，支持生成 GEARS、完整编译、登记、运行及故障诊断。

## 安装 Skill

### 方式一 · 使用 `npx skills`

在终端运行对应宿主的命令。`--global` 安装到个人范围，在本机的其他项目也能使用。 [skills CLI 参数](https://github.com/vercel-labs/skills/blob/main/docs/commands.md#skills-add)

```sh
# Codex
npx skills add yangbobo2021/playbook-slc --global --agent codex --skill playbook-slc

# Claude Code
npx skills add yangbobo2021/playbook-slc --global --agent claude-code --skill playbook-slc
```

### 方式二 · 用 Prompt 让 Agent 安装

把对应的 Prompt 发到本机的 Codex 或 Claude Code 对话里，Agent 会执行安装并核对目录。

**Codex** · [个人 Skill 目录](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)

> 请从 GitHub 仓库 `yangbobo2021/playbook-slc` 安装 `playbook-slc`，让它在我本机的所有 Codex 项目中可用。请安装完整 Skill 目录，包括 `SKILL.md`、`scripts/` 和 `references/`，并确认个人 Skill 目录中的实际安装路径。

**Claude Code** · [个人 Skill 目录](https://code.claude.com/docs/en/skills#choose-where-skills-load)

> 请从 GitHub 仓库 `yangbobo2021/playbook-slc` 安装 `playbook-slc`，让它在我本机的所有 Claude Code 项目中可用。请安装完整 Skill 目录，包括 `SKILL.md`、`scripts/` 和 `references/`，并确认个人 Skill 目录中的实际安装路径。

已有本地工作副本时，可以在 Prompt 里给出仓库的绝对路径，请 Agent 建立符号链接。链接会读取本地工作副本的内容。从 GitHub 安装则会使用远端已有的版本。

安装后在新对话中直接描述任务。需要显式调用时，Codex 用 `$playbook-slc`，Claude Code 用 `/playbook-slc`。 [Codex Skill](https://learn.chatgpt.com/docs/build-skills) · [Claude Code Skill](https://code.claude.com/docs/en/skills)

### 命令运行环境

- 本机需要可用的 Codex 或 Claude Code 及其认证。编译和登记时，Skill 自动识别宿主，优先沿用可确认的会话模型，其次使用宿主配置。运行已登记的工作流时，使用该登记的运行配置。
- Skill 的命令脚本使用 Python 3.11 或更新版本。SLC 和 Playbook 使用 Node.js，SLC 0.23.0 要求 Node.js 25.5.0 或更新版本。
- 支持 SLC `>=0.23.0` 和 Playbook `>=19.1.0`。缺少当前请求所需的包时，Skill 会通过 npm 按需安装。已有版本满足下限就复用，升级已有全局包前会请你确认。单独生成 GEARS 或编译只需调用 SLC。

## 可以做什么

在目标项目的会话里用自然语言提出请求，也可以在请求中写明目标目录和输入文件。

| 你可以这样说 | Skill 会做什么 |
| --- | --- |
| 安装 SLC 和 Playbook。 | 检查本机版本，按需安装所缺的工具。 |
| 在这个目录为 `flow.md` 只生成 GEARS。 | 运行 `slc playbook.text2gears`，返回实际生成的 `flow.playbook/flow.gears.md`，在该阶段结束。 |
| 把这个 `flow.md` 编译成可运行的工作流。 | 在指定目录运行 `slc playbook`，返回实际生成的 `flow.mjs`。 |
| 登记刚才生成的 `flow.mjs`。 | 调用 `playbook add`。已有同名登记时，请你选择沿用、替换或改名。 |
| 用已登记的 `/flow` 核对 `orders.csv`。 | 调用 `playbook run`，把 CSV 路径作为本次输入。 |
| 编译 `flow.md`，登记后用 `orders.csv` 跑一次。 | 依次编译、登记和运行，前一步成功后才继续。 |
| 分析这次 SLC 编译错误，给出修改建议。 | 核对日志、产物和对应版本源码，定位失败阶段，说明怎么改和如何复验。 |

### 生成的文件在哪里

SLC 从用户指定的目标目录启动。以 `flow.md` 的完整编译为例，常用产物的位置如下。

```text
目标目录/
├── flow.mjs                 Playbook 登记入口
└── flow.playbook/
    ├── flow.gears.md         流程规则
    ├── flow.fsm.ts           状态机
    ├── flow.playbook.ts      运行时包装
    └── package.json          SLC 生成的运行环境配置
```

同目录重新编译时由 SLC 复用或更新同路径产物。登记前若原入口和新入口是同一路径，Skill 会说明重新编译可能已更新该文件。 [CLI 操作与确认规则](references/workflow-cli.md)

### 进度和失败怎么看

Skill 在编译、登记和运行过程中说明做到哪一步。超时时会交代已保留的文件、当前等待点，以及业务结果是否已从本次运行记录确认。比如订单已核对但 CLI 最终回复超时时，会分别报告已核实的业务结果和回执状态。 [进度与超时](references/workflow-cli.md#进度与超时)

诊断可从一段已保存的报错或审计日志开始。要求实际修改时，Skill 会先备份，并根据证据建议或执行对自然语言描述、生成产物的修改。 [诊断与修复说明](SKILL.md#诊断与修复)

## 包含什么

- Skill 的任务规则与命令入口。 [SKILL.md](SKILL.md) · [workflow_cli.py](scripts/workflow_cli.py)
- 只读证据检查、修改前备份和参考文件校验工具。 [inspect_case.py](scripts/inspect_case.py) · [backup_case.py](scripts/backup_case.py) · [sync_references.py](scripts/sync_references.py)
- 随仓库提供的版本化源码与规约，附提交、版本、来源和文件哈希。 [源码索引](references/source-map.md) · [参考清单](references/upstream/manifest.json)

切面采样于 2026-11-19，包含当时远端 main 的 SLC 0.23.0 与 Playbook 19.1.0；完整提交见 manifest。诊断以实际执行版本为准，更新快照按[刷新说明](references/source-map.md#刷新参考快照)。

## 修复结果如何解释

分开记录原描述直接通过、修改描述后通过、手修产物后通过、仍失败/未验证。允许为尽量编译成功而改变流程，但必须说明行为差异并保留用户核心目标。未执行复验只称“待复验”；编译成功不等于启动成功或业务结果正确。Skill 不默认修改 SLC/Playbook 程序或关闭检查器。

完整编译可能很慢。本仓库验收使用离线测试和既有失败证据，不启动新的真实编译。实际复验须先指定假设、阶段、时间预算和日志位置；没有新信息时不反复重跑。

## 验收

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_references.py --verify
```

测试覆盖命令路由、输出路径、进度和失败处理，以及审计读取、备份和源码快照校验。离线测试用于核对这些行为，真实编译、登记和运行另按验收用例验证。 [工作流验收](acceptance.md) · [诊断验收](tests/ACCEPTANCE.md)

本仓库使用 Apache-2.0。第三方切面保持原文与原有版权；来源和许可见 [NOTICE](NOTICE) 及各上游 LICENSE。
