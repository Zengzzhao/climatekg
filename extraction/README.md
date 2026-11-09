# 抽取工程目录

`extraction/` 维护当前科学计量抽取流程。`experiments/` 下保留旧基线、气候领域工具和实验入口。

```text
extraction/
├── extract_md_deepseek.py       新流程主入口
├── batch_extract.py            科学计量批量入口
├── check_api.py                 DeepSeek 兼容接口检查
├── test_deepseek_relation_judge.py 独立关系裁判实验入口
├── prompts_zh.py               当前共享中文提示词
├── evidence_index.py           原文索引及检索
├── evidence_selection.py       条件路径与证据集合选择
├── relation_evidence.py        证据角色、补检及验证
├── evidence_schema.py          证据数组与原文校验
├── scientometrics_ontology.py  当前本体与互斥规则
├── validation_agent.py         当前Validator
├── evaluation_agent.py         当前Evaluator（仅评估）
├── triplet_metrics.py          C_triplet与阶段统计
├── stats.py                    已保存结果统计
├── .env / .env.example         共享配置，密钥不进入报告
└── experiments/
    ├── migrated_baseline/
    │   ├── run.py              严格旧流程对照入口
    │   ├── __main__.py         模块启动入口
    │   ├── manifest.json       旧源码来源与SHA256
    │   ├── frozen/             cb2e962的8份原始计算模块
    │   └── README.md           对照配置及命令
    ├── climate_baseline/       原气候工具、本体、Schema与提示词
    ├── karma/                  保留的KARMA比较
    ├── full_first_paper.py     新流程的第一篇完整实验
    └── stage_ablation.py       已存阶段结果的消融统计
```

## 启动

从仓库根目录执行，新流程命令不变：

```bash
.venv/bin/python extraction/extract_md_deepseek.py --help
.venv/bin/python extraction/experiments/full_first_paper.py --help
```

旧流程命令改为：

```bash
.venv/bin/python extraction/experiments/migrated_baseline/run.py --check-only
```

也支持：

```bash
.venv/bin/python -m extraction.experiments.migrated_baseline --check-only
```

正式旧流程实验继续传入 `--markdown` 和新的 `--output`；其他参数、严格旧字符串证据行为及最新提示词读取方式保持不变。旧的experiments/migrated_baseline.py文件已移走，不再使用旧路径。详细说明见 [迁移基线](experiments/migrated_baseline/README.md)。

按年度目录逐篇处理 `<论文标题>/full.md` 可使用同目录 `batch.py --input-root <年度目录> --output <新批次目录>`。
先加 `--dry-run` 扫描清单；正式串行运行支持跳过已完成论文和显式重试失败项，详见迁移基线说明的批量章节。
先处理前50篇、后续同目录扩展时使用 `--stop-after 50`；它保留完整清单，与会截断清单的旧 `--limit` 不同。

离线检查入口参数和旧基线源码清单。

```bash
.venv/bin/python extraction/batch_extract.py --help
.venv/bin/python -m extraction.experiments.migrated_baseline --check-only
```

## CUC模型API接入

按照《中国传媒大学大模型服务使用指南》的OpenAI接口配置，继续复用现有DEEPSEEK变量名；新旧流程都读取 `extraction/.env`：

```dotenv
DEEPSEEK_API_KEY=<个人CUC API密钥>
DEEPSEEK_BASE_URL=https://openai.cuc.edu.cn/v1
DEEPSEEK_MODEL=cuc/deepseek
```

基址保留 `/v1`，不要再追加 `/chat/completions`。校外按指南连接学校VPN。Shell中已有的同名环境变量优先于.env；切换配置后应检查是否仍有旧变量覆盖。

```bash
.venv/bin/python extraction/check_api.py
```

此命令发送两个小请求，分别验证正式抽取使用的JSON模式、16384输出上限与thinking参数，以及Validator/Evaluator使用的valid标签格式。可加 `--output <报告.json>` 保存不含密钥的结果。2026-11-05实测CUC的 `cuc/deepseek` 两项均通过；独立验证器与评估器也将 `cuc/` 模型路由到Chat Completions。旧流程冻结模块未修改，其主入口原本已使用该协议。

切换服务、模型或提示词后使用新的批次目录，例如：

```bash
.venv/bin/python extraction/experiments/migrated_baseline/batch.py \
  --input-root '/Users/zengzizhao/Desktop/毕设论文/数据集md/2024' \
  --output reports/migrated_baseline_strict_2026-11-05/2024_cuc_run1 \
  --model cuc/deepseek \
  --stop-after 50
```

新目录会重新运行前50篇，不复用原 `2024_all` 的模型结果。后续在配置保持一致的前提下可增加stop-after续跑。API连通检查不启动论文批次，也不代表抽取质量评估。

## 依赖与隔离

- 新流程不依赖两个baseline目录中的计算实现。
- migrated_baseline/frozen中的源码与cb2e962逐字一致，不在其中开发新方法。加载前验证清单，运行时无需Git；最新提示词由启动器注入，不把旧提示词覆盖到根目录。
- climate_baseline保留原气候工具。其跨文件引用使用明确的包路径，避免与根目录同名模块混淆；KARMA的气候裁判提示词引用同步指向该目录。
- 配置与历史数据位置保持在extraction根目录。新旧对照必须指定不同实验输出目录；每轮保存提示词与源码快照。
- 当前目录测试不等于在线抽取质量或完整气候流程的验证。工程整理期间没有调用模型API。

## 新流程指定目录批量运行

### 联合选证与定向补检（2026-11-05 更新）

默认 `coherent` 选证由模型联合审阅检索池中的原文，不再使用角色覆盖增益／长度的贪心评分。
选择时优先明确绑定准确端点及方向的关系陈述，再保留定义、版本、指代先行词、归属、条件与反证，
在最多 6 段、14000 原文字符的预算内组成读者能独立理解的证据集合。角色标注仅供阅读参考，
不再以角色覆盖布尔值阻止 Validator 阅读证据。选择器的充分性判断也不能替代 Validator。
`cover` 参数作为新联合选证的兼容别名，历史贪心实现只存在于历史源码快照；`topk` 仍供消融使用。

Validator 一次调用返回 `verdict`、`reason`、`missing_evidence`、`search_queries`。
首次拒绝后，只补检一次：使用具体缺失内容和原文语言检索词召回未看过的单元（短名单最多为 pool_size 的三倍），
再将缺失需求与已有证据一起交给模型定向挑选新单元，并在角色标注、联合重新选证时继续提供同一需求。
新旧检索池合并后重新选证、重新验证；第二次拒绝即停止，没有新单元也停止，不在同一证据上反复投票。
联合重选可保留必要旧片段、替换不充分片段，并受同一预算约束，不机械拼接导致超预算。

只有选定的原文数组进入 Validator/Evaluator；拒绝理由、检索需求、角色说明均不作为证据。
`evidence_completion.trace` 保存每轮判定、反馈、选择理由、检索词和补检单元；`supports` 仅包含最终保留单元的标注，
`pool_supports` 保留完整池标注供审计。非法 JSON、未知单元、重复单元、超预算或截断响应是执行错误，不计为语义拒绝。
这些改动改善证据组织及诊断，不保证候选被通过；反证和不足应继续被拒绝，Evaluator 保持独立。

算法更新后请使用新的实验输出／批次目录。历史结果及 frozen 旧流程不会被重写；旧批次不能混入新算法续跑。

当前新方法的独立批量入口为batch_extract.py，调用experiments/full_first_paper.py。每篇从全文重新抽取元数据、实体、候选与详情，候选直接进入本体检查、联合选证及一次定向补检，模型仍使用共享.env配置。论文之间串行，workers仅控制每篇内部验证／评估并发。

```bash
.venv/bin/python extraction/batch_extract.py \
  --input-root '/Users/zengzizhao/Desktop/毕设论文/数据集md/2024' \
  --output reports/new_pipeline_2026-11-05/2024_all \
  --stop-after 50 \
  --workers 2
```

先加--dry-run只显示计划，不调用API、不创建输出目录。旧新批量入口使用同一排序与相对路径paper_id规则，因此同一输入目录前50篇一致；也应核对两组manifest中文档SHA256和实际提示词快照。

输出位于papers/<paper_id>/attempt_001，包含summary.json、final_graph.json、05_evaluated.json、code及api_calls；范围汇总为first_50_summary.json／first_50_report.md。全批进度为batch_summary.json／report.md。已完成项跳过，失败项需显式retry-failed并保留原尝试；扩大stop-after或去掉参数可继续处理同清单未完成论文。旧新结果必须使用不同根目录。

新方法以direct_candidates汇总候选数，读取历史汇总时兼容原expanded_candidates字段，从每篇table3.json读取阶段表。该表仍是按最终标志的回溯统计；条件路径及后置互斥的具体停止原因应查看05_evaluated.json，不把后置互斥计作初始端点检查的独立效果。

## 独立关系裁判实验

`test_deepseek_relation_judge.py` 逐条判定已有 JSON 中的 `relation` 或 `relations` 候选，输出到单独的 JSON。可用 `--prompt-file` 指定系统提示词，用 `--include-reasoning-chain` 附加英文推理链。主流程的 Validator 在 `validation_agent.py` 中，接受原文证据并提供补检反馈。

```bash
.venv/bin/python extraction/test_deepseek_relation_judge.py \
  --input /path/to/relations.json --output /path/to/judged.json
```
