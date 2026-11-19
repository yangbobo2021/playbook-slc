<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Boboyang -->

# 安装、编译、登记与运行

在用户目标项目下调用 `scripts/workflow_cli.py`。路径按当前项目解析，也可以用全局选项 `--cwd` 指定项目。以下示例中的脚本路径换成实际 Skill 路径。Python 3.11 及以上读取 Codex 的 TOML 配置，登记使用本机 Node 和已安装的 Playbook 自带的 YAML 与目录验证。

| 用户请求 | 助手调用 |
| --- | --- |
| 安装 SLC 和 Playbook | `python3 <skill>/scripts/workflow_cli.py install` |
| 只编译描述 | `python3 <skill>/scripts/workflow_cli.py compile flow.md` |
| 登记指定入口 | `python3 <skill>/scripts/workflow_cli.py register '/实际入口/flow.mjs'` |
| 运行现有登记 | `python3 <skill>/scripts/workflow_cli.py run /flow 'orders.csv'` |
| 编译、登记后运行 | `python3 <skill>/scripts/workflow_cli.py workflow flow.md 'orders.csv'` |

按当前会话识别宿主。脚本检查最近的宿主进程和会话标志。若工具执行隔离导致标志不可见，助手依据自己的实际宿主上下文在子命令前传 `--host codex` 或 `--host claude-code`。已安装的 Agent 命令仅表示它可用。能确认当前会话模型名可供该 CLI 使用时传 `--session-model <型号>`。其余情况省略此选项，采用宿主已有配置或原生模型默认值。Codex 在 SLC 和 Playbook 两侧均用 `codex`，Claude Code 则分别用 `claude-code` 和 `claude`。运行现有登记使用登记中的配置。

SLC 最低为 `0.23.0`，Playbook 最低为 `19.1.0`。缺包时用 npm 全局安装最低支持版本，已装且满足下限的版本直接复用。若结果为 `upgrade-confirmation-required`，向用户说明各包的当前版本和拟升级版本。用户同意后在子命令前传 `--approve-upgrade slc=<旧版本>` 或 `--approve-upgrade playbook=<旧版本>`；同时获准升级两个包时分别传入。

每次编译在项目的 `.playbook-slc/builds/` 下使用独立输出目录，以保留旧登记仍在引用的产物。只将 SLC 回执中实际出现的入口交给登记。结合历史耗时按需传 `--timeout <秒数>`，默认 300 秒。操作按命令结果顺序推进。

结果为 `conflict` 时，提示原有和拟登记的入口，请用户选择沿用、替换或用新名称登记。记录 `result.token`。拿到决定后，复用 `result.proposed.entry` 或 `stages.compile.entry`，执行 `register <入口> --collision reuse|replace|rename --token <值>`，改名时加 `--name <新名称>`。原请求还包括运行时，用登记结果的 `name` 和 `entry` 执行 `run <名称> <原输入> --expected-entry <入口>`。等待期间先保留原有登记和未完成的请求。登记或产物已变化时，重新核对用户选择。

工具 JSON 包括 `result`、已完成的 `stages` 和实际 `commands`。`run-returned` 表示 Playbook 返回了一个会话回复，按回复和运行证据再判断完成、待答复或失败。`clarification-required` 时把原始澄清事项交给用户。其他失败按实际阶段和回执进入 [诊断](diagnosis.md)。

## 进度与超时

开跑时先用会话消息说明本次在做什么，长操作说明等待上限适用的范围。脚本把日常语言进度实时写到 stderr，stdout 保留最终 JSON。若宿主工具要等命令返回才给出输出，使用该工具的后台执行或分段等待能力，并在子命令前传 `--progress-file <新的本地路径>`。在命令进行时查看文件中新出现的 JSONL 记录并向用户转达。开始、切换步骤、完成关键阶段、长等待和需要确认时更新。重复心跳不必逐条转述。

每条进度有 `message` 和对应证据，最终 JSON 的 `progress` 保留同一串记录。能根据用户请求解释业务字段时，用用户能看懂的话汇报。比如订单报告 `variant=new count=3 total=30` 对应「本次订单已核对，共 3 笔，合计 30」。命令、内部阶段名和日志放在追溯细节里。

编译执行中说明成功发布后才会继续登记和运行。编译超时，说明总预算、最后等待点、已保留的规则和下游操作状态。用回执中的 `completed` 列出已保留规则的实际文件路径或所在目录，并确认是否有本次发布的可运行入口。心跳显示的墙钟时间可能含系统休眠，超时原因只有日志支持时才给出。

运行以 `business.status=confirmed-success` 和其中的 `output` 为本次已核实的工作流终态和产出。脚本以当前命令的会话为起点，只读 Playbook 的会话记录，匹配回合、工作流、入口和实际输入后再认定成功。转述时核对 `output` 是否包含用户请求的结果，缺少的部分要说明。`business` 为空时，`/flow finished`、退出码和最终回复中的自述只能作为观察记录。若业务已确认而 `cli_reply=timed-out`，交付业务结果并说明 CLI 未返回最终回复。证据不足时明确说业务结果未确认。超时后不自动再运行。
