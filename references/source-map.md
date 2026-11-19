<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- SPDX-FileCopyrightText: 2026 Boboyang -->
# 参考源码导航

upstream/ 是原文切面而非摘要，manifest.json 记录提交、版本、来源 URL 和逐文件 SHA-256。snapshot-selection.json 管理范围；不含依赖、用户数据和大型生成工作流。按需读取，不把整套源码加载到上下文。

| 症状 | 优先读取（相对 upstream/） | 搜索词 |
| --- | --- | --- |
| 描述缺规则 | slc/src/clarification.ts；playbook/slc/text2gears.md | clarification、questions |
| 配置和版本 | slc/src/app.ts、config-file.ts、config.ts、runtime-package.ts | selection、stallTimeout、coverage |
| 阶段要求 | playbook/slc/text2gears.md、optimize.md、prefix.md、gears2fsm.md、link.md | Results、Output shall include |
| 检查修正次数 | slc/src/reviewing-agent.ts、phase-runner.ts、managed-compilation.ts | MAX_CHECKING_ROUNDS、mechanicalReview |
| TS 与环境 | slc/src/verify-typescript.ts、isolated-check.ts、check-report.ts | artifact、environment、checker-failed |
| GEARS/FSM 不一致 | slc/src/verify.ts；slc/specs/packages/verification.md | conformance、sourceItem |
| 路径无法验证 | slc/src/verify-coverage.ts | synthesizedFieldValue、resultOutputCandidates、readCoverageWitnesses |
| 用户答复恢复 | playbook/src/xstate-playbook-runtime.ts；playbook/slc/gears2fsm.md | pendingBossQuestion、BOSS_REPLY |
| 写回与提交冲突 | playbook/src/xstate-runtime.ts；playbook/reference/sdlc/code.playbook/bin/repository-effects.js | repositoryDisposition、dispositionMatches |
| 协调锁与权限 | playbook/reference/sdlc/code.playbook/bin/repository-effects.js | playbook-effect-claims、gitDir |
| 加载与启动 | playbook/src/runtime.ts；slc/src/entry-module.ts、runtime-contract.ts、emitted-imports.ts | createRuntime、schema、ABI |
| 产物陈旧 | slc/src/build-history.ts、pin-currency.ts、compiler-identity.ts | manifest、hash、pin |
| 恢复与副作用 | playbook/specs/packages/recovery.md、playbook-runtime.md | checkpoint、effectLedger、replay |

SLC 的 pipelines/playbook 是编译侧参考，Playbook 的 slc/ 是发布阶段定义；本次实际运行也可能来自安装包。以本次审计捕获的定义为先。原文链接可能指向未收录文件，按 manifest 的固定提交 URL 补查，不把未收录当作不存在。

版本号一致只说明版本标签一致，不证明 Git 提交或安装字节完全相同。需要精确确认时比较实际文件哈希和本次审计的编译器 buildIdentity；不把 version_match 当作实现一致性证明。

## 刷新参考快照

先获取两个 checkout 的远端 main，再运行同步工具；工具不联网、不 checkout、不改上游工作区、不安装依赖：

```sh
git -C /path/to/slc fetch origin main
git -C /path/to/playbook fetch origin main
python3 scripts/sync_references.py \
  --slc /path/to/slc --slc-ref origin/main \
  --playbook /path/to/playbook --playbook-ref origin/main
python3 scripts/sync_references.py --verify
```

远端不维护 origin/main 时传入刚取得的完整提交号，不误用其他分支的 FETCH_HEAD。工具从 Git 对象复制，忽略未提交改动。刷新后审阅和提交差异；历史问题仍按历史版本解释。manifest 的时间是取样时间，不证明以后仍是最新版本。
