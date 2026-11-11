# 科学计量关系抽取与验证交接

2026-11-09，Asia/Shanghai。工作区 `/Users/zengzizhao/Desktop/climatekg`，分支 `feature/exp1`，HEAD `2811661`。这是本轮 `x_d(FN)-index` 实验的接续文件。此前的证据检索研究背景另见[上一份交接](/Users/zengzizhao/Desktop/climatekg/SESSION_HANDOFF_2026-11-09_EVIDENCE.md)。

## 接手要解决什么

开发样本是第一篇 `x_d-index and its variants`。目标是从全文抽出 `x_d(FN)-index → x_d-index` 的 `alternative_to` 和 `improves_on`，选出能绑定两个具名端点的证据，并通过 Validator。两条关系在当前候选提示词下已连续五轮抽出，但两次全流程均未保留。下一步应先固定候选和已记录的原文片段，区分证据组织问题与 Validator 判据执行问题。

全文路径为 `/Users/zengzizhao/Desktop/毕设论文/数据集md/2024/-index and its variants  a set of overall scholarly expertise diversity indices for the research portfolio management of institutions/full.md`。用户认可有原文依据的条件替代和方法设计改进。具名变体明确属于规则覆盖的组即可应用通用替代条件，仍须保留条件。具名变体明确针对原指标的局限提供应对设计即可支持 `improves_on`，可与 `variant_of` 并存。是否在本样本满足替代条件、是否观察到数值改善，应另行记录。

## 当前工作区与运行版本

- 交接前，已有 `SESSION_HANDOFF_2026-11-09_EVIDENCE.md` 暂存为新增。`extraction/prompts_zh.py` 同时有暂存和未暂存改动。暂存层含旧流程抽取和 Validator 规则，以及新 Validator 的条件替代规则。未暂存层含第二版新候选提示词。`extraction/relation_evidence.py`、`extraction/README.md` 也有未暂存改动。接手时以 `git status --short`、`git diff` 和 `git diff --cached` 核对，保留现有改动。[提示词](/Users/zengzizhao/Desktop/climatekg/extraction/prompts_zh.py)
- 工作区当前提示词与最近一次新流程复跑的提示词快照一致。当前 `relation_evidence.py` 比那次快照多了 `inline_trace_evidence()`，将 `retrieved_evidence`、`selection.evidence` 以及补检的 `shortlist_evidence`、`selected_evidence` 内嵌到 trace。顶层 `evidence` 仍是最终送验数组。这项变化用于审计，未在最近一轮全流程中运行。`improves_on_with_evidence_text.json` 可用来查看补齐文字后的历史 trace。[当前 trace 输出代码](/Users/zengzizhao/Desktop/climatekg/extraction/relation_evidence.py:148) · [带原文的历史 `improves_on` 记录](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_repeat_2026-11-09_e5A7Iq/improves_on_with_evidence_text.json)
- 新流程从元数据、实体、候选和指标详情四次全文请求开始，候选阶段使用实体清单。后续是本体、候选独立检索、角色标注、`coherent` 选证、逐字核验、Validator 和可选的一次补检。角色覆盖率目前是诊断量。`variant_of`／`derived_from` 的竞争在验证后处理。最终保留依据 `ontology_conformed AND evidence_validated AND valid`，Evaluator 单独评估。`alternative_to` 作为对称关系在本体阶段可能交换两端，比较时按无向对匹配。[新流程入口](/Users/zengzizhao/Desktop/climatekg/extraction/experiments/full_first_paper.py) · [检索与选证流程](/Users/zengzizhao/Desktop/climatekg/extraction/relation_evidence.py:164) · [本体处理](/Users/zengzizhao/Desktop/climatekg/extraction/scientometrics_ontology.py:25)
- 单篇入口会检查运行配置和源码快照。源码或提示词变化后使用新输出目录。`reports/` 被 Git 忽略，当前可用的核心产物见下文，上一份交接引用的部分早期实验目录已不在当前工作区。已完成的 DeepSeek 授权限于各自实验，接手续跑前核对新的外发范围。服务为 `api.deepseek.com`，模型 `deepseek-flash`。凭证留在本地配置中。

## 已核实的实验

| 新流程版本 | `alternative_to` 候选命中 | `improves_on` 候选命中 | 全流程结果 |
|---|---:|---:|---|
| 候选提示词修改前 | 0/1 | 0/1 | 14 条候选，13 条保留。目标关系未送验 |
| 第一版 | 2/5 | 2/5 | 纯抽取对照 |
| 第二版 | 5/5 | 5/5 | 预先选定的第 1 轮两条均 `invalid`。16 条候选，12 条保留 |
| 第二版独立全流程复跑 | 1/1 | 1/1 | 两条均 `invalid`。16 条候选，12 条保留 |

第二版在 `CANDIDATE_RELATIONS_PROMPT` 中要求每个标签独立判断，将通用替代条件展开到有原文归属依据的具名变体，并从原指标的具体局限反查应对设计。示例用的是通用名称，目标三元组只用于事后比对。第二版五轮也都漏掉了修改前那轮抽出的 `xx_d-index → derived_from → x_d-index` 和 `xx(g)_d-index → derived_from → x_d-index`，应作为候选回归观察项。这是单篇开发样本的重复试验。[修改前全流程](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_2026-11-09_505lj9/target_pair_summary.json) · [第一版抽取对照](/Users/zengzizhao/Desktop/climatekg/reports/new_candidate_fn_prompt_v1_2026-11-09_QXWpxJ/comparison.json) · [第二版抽取对照](/Users/zengzizhao/Desktop/climatekg/reports/new_candidate_fn_prompt_v2_2026-11-09_7ZZw69/comparison.json) · [第二版首轮全流程](/Users/zengzizhao/Desktop/climatekg/reports/new_candidate_fn_prompt_v2_2026-11-09_7ZZw69/round_01/target_pair_summary.json)

旧流程对照用冻结实现和当前中文提示词，关系请求独立识别端点并同时生成连续 `paragraph`。一轮旧全流程抽取 16 条、保留 13 条，目标 `alternative_to` 当时被判 `valid`，`improves_on` 为 `invalid`。前者的单句只提到“任一变体”，缺少具名归属，属于误通过。收紧旧 Validator 后，四组证据单独验证的结果是两段旧抽取证据均 `invalid`，两组补齐具名端点的多段证据均 `valid`。多段英文证据在该对照中按原顺序拼成一个 `paragraph` 字符串直接送验，未走旧流程的连续原文证据核验。该结果只用来诊断 Validator 对证据完整性的反应。[旧全流程阶段索引](/Users/zengzizhao/Desktop/climatekg/reports/legacy_full_fn_prompt_v1_2026-11-09/stage_index.json) · [旧 Validator 四组对照](/Users/zengzizhao/Desktop/climatekg/reports/legacy_validator_fn_four_cases_2026-11-09_FV6zhg/results.json)

## 最新复跑的具体失败位置

两条目标均通过本体及逐字原文核验。`evidence_validated=true` 表示选中的片段确实来自原文，语义充分性由 Validator 判断。以下 ID 只对应这篇全文和本轮切分。逐轮取证与判定可从[最新目标摘要](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_repeat_2026-11-09_e5A7Iq/target_pair_summary.json)和[完整验证输出](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_repeat_2026-11-09_e5A7Iq/04_validated.json)查看。

| 证据单元 | 内容 |
|---|---|
| `e0005` | 摘要列出两个变体及其设计目的 |
| `e0027` | 原 `x_d-index` 使用完整引文计数时可能产生领域偏差 |
| `e0028` | 引文归一化可缓解领域偏差，并用于替代完整计数作为网络权重 |
| `e0033` | 命名领域归一化构造为 `x_d(FN)-index` |
| `e0039` | 对原指标及其变体的条件替代规则 |
| `e0062` | 当前数据中 FN 变体的 Spearman 秩相关系数为 `0.888`，作者继续采用原指标 |

- `alternative_to` 首轮选中 `e0005/e0027/e0033/e0039/e0062`，Validator 为 `invalid`。它承认一般替代规则，却要求 FN 变体在当前数据中落入满足条件的子集。上一轮也援引 `0.888` 拒绝。需要区分“属于规则适用的具名变体组”和“当前样本满足条件 C”。当前新 Validator 提示词已写明这一区别，实跑仍未遵循。[新 Validator 提示词](/Users/zengzizhao/Desktop/climatekg/extraction/prompts_zh.py:113)
- `improves_on` 首轮选中 `e0005/e0027/e0039/e0062`，Validator 为 `invalid`。这次补检加入并选中了 `e0028/e0033`，原指标局限、应对设计、具名变体三类片段已进入同一请求，仍为 `invalid`。模型把归一化视为简单适配而只认可 `variant_of`，并引用 `0.888` 否定改进。上一轮补检后选择没有改变，因此两次失败的细节不同。后续需用同一候选和证据测试设计改进与实测差异是否被混淆。[两轮原文与 Validator 反馈](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_repeat_2026-11-09_e5A7Iq/improves_on_with_evidence_text.json)
- 补检的独立工程问题是模型反复返回 `existing_evidence` 中的 ID，而它们不在补检 `excerpts` 短名单中，触发 `Targeted retrieval returned malformed selection`。最近一轮有四份此类响应。此次恢复只在运行目录的缓存回放中移除了已选重复 ID，保留合法新增 ID 和原始响应。没有合法新增时沿用首轮判定。当前源码仍会拒绝短名单外 ID，新增的 trace 原文输出并未修复此行为。[补检实现](/Users/zengzizhao/Desktop/climatekg/extraction/relation_evidence.py:115) · [最新恢复记录及原始响应索引](/Users/zengzizhao/Desktop/climatekg/reports/new_full_fn_repeat_2026-11-09_e5A7Iq/recovery.json)

## 下一会话可直接开始的对照

1. 固定两条候选、模型和 Validator 入口。先回放短证据作为负对照，再分别用原文 `e0005/e0033/e0039` 测条件替代、用 `e0027/e0028/e0033` 测设计改进。各做加入和不加入 `e0062` 的成对比较，检验实测无显著差异是否改变结论。保留端点、版本、任务和原文条件，记录完整理由。可再用错版本、只给通用替代规则、只给归一化定义等负例检查边界。
2. 若最小原文证据组仍误拒，优先调整新 Validator 的判定步骤。条件替代分开核对成员资格、规则和当前样本状态。改进分开核对原指标局限、具名变体的应对设计和实际效果，明确 `variant_of` 与 `improves_on` 可并存。固定 Validator 对照通过后，再比较现有选证与按角色组装证据，保留证据预算和候选集作为控制变量。
3. 单独验证补检异常处理。已有 ID 与未知 ID 应区分，合法新增项和错误记录都要保留。纯重复返回应有可审计的无新增处理。将源码修改、手工回放和正常运行分开记载。最后再做全流程复跑，同时检查两条目标和两条历史 `derived_from`。

本次交接只核对了工作区和既有产物，未发起模型请求。下一会话可从最新 `target_pair_summary.json`、`improves_on_with_evidence_text.json` 和 `recovery.json` 开始。
