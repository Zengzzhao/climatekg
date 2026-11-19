# 抽取工程目录

`extraction/` 维护当前科学计量抽取流程。`experiments/` 下保留新流程的批量执行入口、旧科学计量基线，以及独立的气候工具和发布结果分析。下文带日期的实验数字是历史记录；相关 `reports/` 是本地实验产物，仓库不附带这些报告或回归测试。

```text
extraction/
├── extract_md_deepseek.py       新流程主入口
├── batch_extract.py            科学计量批量入口
├── check_api.py                 模型服务兼容性检查，不处理论文
├── test_deepseek_relation_judge.py 独立关系裁判实验入口
├── prompts_zh.py               当前共享中文提示词
├── compact_evidence.py         主流程默认检索入口
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
    │   ├── batch.py            旧流程批量对照入口
    │   ├── __main__.py         模块启动入口
    │   ├── manifest.json       旧源码来源与SHA256
    │   ├── frozen/             cb2e962的8份原始计算模块
    │   └── README.md           对照配置及命令
    ├── climate_baseline/       原气候工具、本体、Schema与提示词
    ├── karma/                  保留的KARMA比较
    ├── full_first_paper.py     新流程完整单篇入口，供 batch_extract.py 调用
    └── stage_ablation.py       已存阶段结果的消融统计
```

## 启动

从仓库根目录运行以下离线检查，不调用模型 API：

```bash
.venv/bin/python extraction/extract_md_deepseek.py --help
.venv/bin/python extraction/experiments/full_first_paper.py --help
.venv/bin/python extraction/batch_extract.py --help
.venv/bin/python -m extraction.experiments.migrated_baseline --check-only
.venv/bin/python extraction/experiments/migrated_baseline/batch.py --help
```

旧流程单篇使用 `python -m extraction.experiments.migrated_baseline --markdown <论文 Markdown> --output <输出目录>`，也可直接运行该目录的 `run.py`。详细说明见[迁移基线](experiments/migrated_baseline/README.md)。

按年度目录逐篇处理 `<论文标题>/full.md` 可使用同目录 `batch.py --input-root <年度目录> --output <新批次目录>`。
先加 `--dry-run` 扫描清单；正式串行运行支持跳过已完成论文和显式重试失败项，详见迁移基线说明的批量章节。
先处理前50篇、后续同目录扩展时使用 `--stop-after 50`；它保留完整清单，与会截断清单的旧 `--limit` 不同。

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

### 默认检索与语义分片

单篇主入口、完整单篇实验和批量入口统一使用语义分片：普通正文一完整句一个单元，
行间公式与前面的引导文字及紧随的 where 等变量定义绑定为同一单元，整表保持完整。
例如 `in the following way:`、`as follows:` 后隔空行出现 `$$...$$` 或 `\[...\]`，
仍与引导句合并；保留跨行原文及精确字符偏移。不强行拼接无关句子或跨章节合并。

新流程已删除原 350/500 字符窗口、重叠、多句组包及重切策略，不设单片固定字符上限，
不拆句、不拆公式块、不拆表格行。普通稀疏入口和定向补检使用同一分片器；
角色标注也不能重新拆开公式块。历史运行结果与冻结基线保留供复核。

`compact_evidence.py` 保留入口兼容名称，运行配置记录 `semantic-chains` 与完整 chunking 策略。
关系条件检索仍使用 proof_v2、BM25/固定 BGE 融合（向量权重0.8），首轮最多12个原文单元。
后续运行角色标注、6段核心选证加最多2段相邻上下文、原文匹配及逐条件Validator，
并按 evidence-rounds 控制一次可选补检。选证总字符预算仍为14000；该预算限制选择，
不会把超长语义单元切碎。当前不执行全文证据补查。

Validator 要求明确的当前作者归属，排除并列聚合指标之间的伪组成关系；
对通过初审的 improves_on 独立核对具名来源的设计与缓解机制，防止不同变体借用证据。
实验与消融、完整参数和原始 API 记录见 `reports/xd_annotation_tuning_2026-11-12/REPORT.md`。

安装 `requirements.txt` 后，将固定 BGE 的 `model.onnx` 和 `tokenizer.json`
放在 `extraction/embedding_files/bge-small-en-v1.5/`。
也可设置 `BGE_MODEL_DIR`，或传 `--compact-bge <目录>`。
加载器核对两个文件的 SHA-256，缺失权重或依赖时在论文请求开始前报错。
本地模型目录被 Git 忽略，权重不随仓库提供。

```bash
.venv/bin/python extraction/extract_md_deepseek.py --markdown "$PAPER" --date "$RUN_DATE"
.venv/bin/python extraction/experiments/full_first_paper.py --markdown "$PAPER" --output "$OUT"
.venv/bin/python extraction/batch_extract.py --input-root "$CORPUS" --output "$OUT"
```

`--evidence-retrieval sparse` 可运行稀疏检索（共享语义分片）。
单篇旧参数 `--experimental-evidence-bge` 作为 `--compact-bge` 的别名读取。
更换算法后使用新输出目录，实验与批量入口会核对配置和源码快照。

### 联合选证与定向补检（2026-11-05 更新）

2026-11-14 结构探索（候选融合、覆盖/去重、修补、图扩散与多样化束搜索）未替换默认。
当前仍为 `pool_structure=chains`、`beam_policy=score`：55/65，组合MRR=0.437296。
可选端点多样化束策略为 `beam_policy=endpoints`：开发集56/65，但前6位覆盖与MRR略降；
收益仅来自第一篇新增一条关系。10结构内部留一选择为53/65，不支持稳定升级。
负结果、逐关系原文池及完整审计见 `reports/rcec_structure_comparison_2026-11-14/`。

2026-11-14 后续依据用户《抽取规则.md》修订条件检索语义，默认 profile 改为
`context_expansion`，上一轮全部数值参数不变。保留原条件数、端点方向和分层链搜索，
在原查询后追加通用关系条件语义，并增加多词端点的软词项匹配及紧邻指代语境的检索锚点。
软锚点不是实体别名认定或关系验证。整批固定65条关系开发集55/65（上一版50/65），
同配置匹配消融A/B为41/65、45/65，组合MRR分别为0.4373、0.4041、0.4015。
查询开发使用了全部五篇金标，内部留一不属于独立模板泛化验证。失败的整体重写与OR路径合并方案
也保留在 `reports/rcec_rule_queries_2026-11-14_round1/` 和 `round2/`，最终报告在 `round3/`。

2026-11-14 的五篇开发集受限调参保持条件模板、分层评分、链搜索和12单元预算不变。
默认参数更新为 dense=0.8、endpoint_weight=0.65、neighbor_weight=0.2、math_weight=0.15、beam=24；
其余有效权重不变，relation_weight/pool_order_weight 两个实验开关均为0。
开发集完整组合召回50/65，按论文留一选参为48/65，旧RCEC为46/65。
新参数匹配的A/B消融分别为42/65、45/65；完整方法的MRR仍低于匹配B，不能声称所有指标第一。
这些论文存在历史开发使用，留一结果不等于独立测试；冻结配置后需要新论文验证。
搜索方案、每折记录和旧配置快照见 `reports/rcec_grouped_tuning_2026-11-14_round1/`。
历史实验应使用各自源码快照，不能用新默认值混入旧批次续跑。

默认 `coherent` 选证由模型联合审阅检索池中的原文，不再使用角色覆盖增益／长度的贪心评分。
选择时优先明确绑定准确端点及方向的关系陈述，再保留定义、版本、指代先行词、归属、条件与反证，
通用选择器默认最多 6 段、14000 原文字符；当前 compact 入口另预留 2 段，
只从已召回池补齐必要先行语境（公式块及整表已在分片阶段绑定），最终最多 8 段。角色标注仅供阅读参考，
不再以角色覆盖布尔值阻止 Validator 阅读证据。选择器的充分性判断也不能替代 Validator。
`cover` 参数作为新联合选证的兼容别名，历史贪心实现只存在于历史源码快照；`topk` 仍供消融使用。

Validator 一次调用返回 `verdict`、`reason`、`missing_evidence`、`search_queries`。
2026-11-18 起删除同证据初判复核：每轮 Validator 只判断一次，首次拒绝直接进入下述定向补检，
补检并重新选证后再判断一次，仍拒绝即结束；每条候选最多一次补检、两次 Validator 判定。
共享裁判函数中的 Evaluator 也只判断一次，不触发补检。充分路径之间 OR、路径内必要条件 AND 的判据保留。
首次拒绝后，只补检一次：使用具体缺失内容和原文语言检索词召回未看过的单元（短名单最多为 pool_size 的三倍），
再将缺失需求与已有证据一起交给模型定向挑选新单元，并在角色标注、联合重新选证时继续提供同一需求。
新旧检索池合并后重新选证、重新验证；第二次拒绝即停止，没有新单元也停止，不在同一证据上反复投票。
联合重选可保留必要旧片段、替换不充分片段，并受同一预算约束，不机械拼接导致超预算。

只有选定的原文数组进入 Validator/Evaluator；拒绝理由、检索需求、角色说明均不作为证据。
`evidence_completion.trace` 保存每轮判定、反馈、选择理由、检索词和补检单元；`supports` 仅包含最终保留单元的标注，
`pool_supports` 保留完整池标注供审计。非法 JSON、未知单元、重复单元、超预算或截断响应是执行错误，不计为语义拒绝。
每轮的 `retrieved_evidence`、`selection.evidence` 和补检记录的 `shortlist_evidence`、`selected_evidence` 分别内嵌对应片段的原文、章节及全文字符区间。顶层 `evidence` 记录最终实际送验的片段。
这些改动改善证据组织及诊断，不保证候选被通过；反证和不足应继续被拒绝，Evaluator 保持独立。

算法更新后请使用新的实验输出／批次目录。历史结果及 frozen 旧流程不会被重写；旧批次不能混入新算法续跑。

当前新旧方法的抽取统一为2次全文请求：①用相同提示词和Schema联合抽取论文信息、指标实体及属性；②基于联合清单一次抽取全部8种关系。新流程第二次只输出关系，证据交后续检索；旧流程第二次同时输出连续原文paragraph。两边均禁止新增实体端点，统一名称和去重，检查关系标签对应的端点类型。旧消费者需要属性evidence字符串时，仅从联合输出中选择包含属性text的一段原文，不拼接片段。

不执行实体补漏、全文实体/关系复核、逐目标审计或身份锁定。新流程后续保留依据missing_evidence/search_queries定向召回未看过原文的一次补检，不再调用全文编号原文补查。旧流程运行时由启动器接入共享抽取，frozen源码哈希不变；旧本体、证据匹配、Validator和共享Evaluator继续保留。

只运行上述两次抽取、跳过本体/证据检索/Validator/Evaluator：

```bash
.venv/bin/python extraction/experiments/full_first_paper.py \
  --backend zrshuiwu --model deepseek-v4.1-flash-1017 \
  --extraction-thinking disabled --extract-only \
  --markdown "/path/to/full.md" --output reports/new_extract_only_run1
```

`extract_md_deepseek.py`同样支持`--extract-only`。该模式不初始化检索索引，输出原始抽取结果；不会将空证据当成证据检验通过。两次请求属于当前统一配置，后续实验使用新输出目录，不能混合续跑历史13请求结果。

旧流程仅抽取示例：

```bash
.venv/bin/python extraction/experiments/migrated_baseline/run.py \
  --backend zrshuiwu --model deepseek-v4.1-flash-1017 --thinking disabled \
  --extract-only --markdown "/path/to/full.md" --output reports/old_extract_only_run1
```

新旧对照可以每篇共享一次联合清单响应，再独立调用两个关系请求；实际共3次请求/篇，两个分支各对应2阶段。关系结果独立生成，不能复制一个分支的关系给另一个分支。

关系证据原文匹配现与实际旧抽取入口保持相同评分，使用 `RapidFuzz.partial_ratio_alignment`，阈值80，包含边界分数80。对证据数组的每个单元分别与全文评分，全部达到阈值才令 `evidence_validated=true`，不把不相邻片段拼成一个段落后评分。该门控同时用于选证后是否调用Validator及最终原文核验。空数组和空引文不通过。`evidence_match` 保存逐单元得分、判定及严格偏移核对结果；严格偏移仅用于审计，不再作为通过的必要条件。分片与选证仍返回原文单元，指标属性的逐字摘录检查保持原规则。单篇和批量配置均记录 `evidence_matching`；历史实验不重写，新配置使用新输出目录。

重点论文可通过 `--paper-list` 指定 JSON 清单，清单包含 `papers` 数组，每项至少含 `paper_id` 和 `document_sha256`。入口会从 `--input-root` 扫描结果中按清单顺序选择论文，校验原文指纹，拒绝重复项、找不到的论文和已改变的原文。该参数与 `--limit`、`--stop-after` 互斥。可先加 `--dry-run` 查看计划，不调用 API。

```bash
.venv/bin/python extraction/batch_extract.py \
  --input-root '/Users/zengzizhao/Desktop/毕设论文/数据集md/2024' \
  --output reports/new_pipeline_2026-11-05/2024_all \
  --stop-after 50 \
  --workers 2
```

先加--dry-run只显示计划，不调用API、不创建输出目录。旧新批量入口使用同一排序与相对路径paper_id规则，因此同一输入目录前50篇一致；也应核对两组manifest中文档SHA256和实际提示词快照。

输出位于papers/<paper_id>/attempt_001，包含summary.json、final_graph.json、05_evaluated.json、code及api_calls；范围汇总为first_50_summary.json／first_50_report.md。全批进度为batch_summary.json／report.md。已完成项跳过，失败项需显式retry-failed并保留原尝试；扩大stop-after或去掉参数可继续处理同清单未完成论文。旧新结果必须使用不同根目录。

新方法以direct_candidates汇总候选数，读取历史汇总时兼容原expanded_candidates字段，从每篇table3.json读取阶段表。该表仍是按最终标志的回溯统计。

2026-11-11 互斥检查合并到初始本体检查：在端点、类型和方向合法的候选中，同一有向指标对同时出现 `variant_of` 与 `derived_from`，则两条均设置 `ontology_conformed=false`，记录 `relation_constraint_conflict.stage=ontology`，不进入证据检索与 Validator。该规则不再依赖两条关系都通过验证，可能比历史后置互斥多排除候选；Evaluator 仍复评全部候选。历史后置互斥结果保持原样，后续实验须使用新输出目录。冲突可在 `03_ontology.json`（单篇基础入口为 `ontology_conformed_papers`）查看。

## 独立关系裁判实验

`test_deepseek_relation_judge.py` 逐条判定已有 JSON 中的 `relation` 或 `relations` 候选，输出到单独的 JSON。可用 `--prompt-file` 指定系统提示词，用 `--include-reasoning-chain` 附加英文推理链。主流程的 Validator 在 `validation_agent.py` 中，接受原文证据并提供补检反馈。

```bash
.venv/bin/python extraction/test_deepseek_relation_judge.py \
  --input /path/to/relations.json --output /path/to/judged.json
```

## vLLM 服务商接入

远程 vLLM 模型可通过 `--backend vllm` 选择。配置示例见 `.env.example` 的 `VLLM_BASE_URL`、`VLLM_MODEL`、`VLLM_API_KEY`。默认服务模型为 `Qwen/Qwen3-32B-AWQ`，独立连通性检查见 [vLLM 诊断说明](../scripts/README_vllm.md)。也可在 `.env` 设置 `EXTRACTION_BACKEND=vllm`。单次 `--model`、`--base-url` 优先于对应服务的环境配置。

```bash
.venv/bin/python extraction/check_api.py --backend vllm

.venv/bin/python extraction/experiments/full_first_paper.py \
  --backend vllm --workers 1 \
  --markdown "/path/to/full.md" --output reports/new_vllm_run1

.venv/bin/python extraction/experiments/migrated_baseline/run.py \
  --backend vllm \
  --markdown "/path/to/full.md" --output reports/legacy_vllm_run1
```

`extract_md_deepseek.py`、`batch_extract.py` 和 `experiments/migrated_baseline/batch.py` 同样接受 `--backend vllm`。抽取、Validator、Evaluator 共用选中的服务。`--thinking disabled` 对应 `chat_template_kwargs.enable_thinking=false`，`enabled` 对应 `true`。服务端未开启 API key 时可留空 `VLLM_API_KEY`。

2026-11-11 使用 498 字符合成文档实测 API JSON 和标签输出成功，新流程完整执行并保留 2 条关系，旧流程完整执行并保留 1 条关系，Evaluator 分别通过 2/2 和 1/1。结果与日志见 `reports/vllm_smoke_2026-11-11/`。这些记录用于核验服务接入和流程执行。

## ZRSHUIWU 服务商接入

新旧流程均支持 `--backend zrshuiwu`，默认模型为 `deepseek-v4.1-flash-1017`。在 `extraction/.env` 中追加以下独立配置，原有 `DEEPSEEK_*`（含 CUC）和 `VLLM_*` 可继续保留：

```dotenv
ZRSHUIWU_API_KEY=<个人密钥>
ZRSHUIWU_BASE_URL=https://llm.zrshuiwu.com/v1
ZRSHUIWU_MODEL=deepseek-v4.1-flash-1017
```

```bash
# 检查 JSON 抽取与 Validator/Evaluator 标签调用
.venv/bin/python extraction/check_api.py --backend zrshuiwu

# 新流程：关闭抽取思考
.venv/bin/python extraction/experiments/full_first_paper.py \
  --backend zrshuiwu --extraction-thinking disabled \
  --markdown "/path/to/full.md" --output reports/new_zrshuiwu_run1

# 旧流程：关闭抽取思考
.venv/bin/python extraction/experiments/migrated_baseline/run.py \
  --backend zrshuiwu --thinking disabled \
  --markdown "/path/to/full.md" --output reports/legacy_zrshuiwu_run1

# 新旧批量入口使用相同的服务商开关
.venv/bin/python extraction/batch_extract.py \
  --backend zrshuiwu --extraction-thinking disabled \
  --input-root "/path/to/papers" --output reports/new_zrshuiwu_batch1
.venv/bin/python extraction/experiments/migrated_baseline/batch.py \
  --backend zrshuiwu --thinking disabled \
  --input-root "/path/to/papers" --output reports/legacy_zrshuiwu_batch1
```

`extract_md_deepseek.py` 同样支持 `--backend zrshuiwu --thinking disabled`。抽取、选证、Validator 和 Evaluator 共用所选服务商。该服务直接接收 `thinking.type=disabled/enabled`，无需 vLLM 参数转换；新流程抽取默认仍为 enabled，旧流程默认仍为 disabled，裁判各自保持原配置。2026-11-16 的 `hi` 对照测试中，disabled 不返回 `reasoning_content` 且 `reasoning_tokens=0`，enabled 和省略参数则返回思考内容。

不传 `--backend` 时仍遵循已有 `EXTRACTION_BACKEND`，未配置则使用 `deepseek`；也可显式用 `--backend deepseek` 切回原 DeepSeek/CUC 配置，或 `--backend vllm` 使用原 vLLM 配置。`--model` 和 `--base-url` 可覆盖单次运行的模型及地址。切换服务商应使用新的实验输出目录，避免混合续跑。

2026-11-16 接入检查中，JSON 抽取与二元标签请求均通过；新旧批量入口在合成文档上均正确路由至该服务。旧流程记录了两次成功请求，关闭思考时均为 0 思考 token；新流程首次请求及旧流程后续请求被服务商以 HTTP 403 / `insufficient_user_quota` 拒绝，完整流程尚未验证完成。检查报告、实际请求与失败日志保存在 `reports/zrshuiwu_smoke_2026-11-16/`，补充额度后可使用新目录重测。

同日充值后切换 `deepseek-v4.1-flash-1017` 复测，新旧批量流程均完整完成一篇合成文档，分别记录 29、8 次成功请求，关闭思考时均为 0 思考 token。原始记录与身份核查说明在 `reports/zrshuiwu_v41_1017_smoke_2026-11-16/`。调用成功与返回同名 model 字段不能认证底层模型身份，当前未取得可独立核验的上游来源证据。

### 第二篇人工标注增量（2026-11-12）

默认开启 `observed_seeds`，在12单元预算内保留具名数值表行或相关控制变量原文；变量近似只用于召回，不替代验证。修复显示公式与转义下划线的名称规范化，并检查未具名多层级构建与具名公式的语义连接。第二篇金标首轮及最终数组均为13/13条关系、22/22条摘录完整覆盖，第一篇仍为16/16必需金标覆盖。详见 `reports/divergent_annotation_tuning_2026-11-12/REPORT.md`。

### 第三篇类型边界（2026-11-12）

实体与关系提示词区分科研评价测度和普通标题/内容编码属性；数值与回归显著性不自动使属性成为Indicator。新Validator采用明确非指标证据排除，具名数值行无需重复完整定义。用户补充授权后已完成第三篇全文抽取前后对照及生产流程复跑，三条实际关系均通过独立验证；脏实体清单的全文压力测试仍有六条错误属性边，结果与局部实验分别报告。摘录级对照、固定候选证据验证及回归见 `reports/title_elements_tuning_2026-11-12/REPORT.md`。

### 第五篇 Geographical titles 增量（2026-11-12）

当前使用与仅研究问题分开判断：纯RQ不能替代执行证据，明确we follow ... in using ... for the current study仍有效。具名正式发展/健康等复合指数不能与地点编码属性混同。本篇现有检索和最终数组均完整覆盖7条金标，默认检索参数保持不变。旧二元Validator提示词保持本轮开始前版本。实验见 `reports/geographical_titles_tuning_2026-11-12/REPORT.md`。

### 统一金标Evaluator（2026-11-12）

Evaluator只读取候选与送验原文，不读取valid/real/comment、检索轨迹或Validator判定。DeepSeek入口逐条件独立判断，并保存evaluation_details；仍保留原valid，仅更新eval。旧冻结启动器通过适配层复用同一Evaluator，冻结文件未改。

普通论文使用默认语义模式。有明确人工必需/可选证据时，独立CLI可传 `--evidence-rubric <规则JSON>`，按候选source/relation/target精确键执行本地OR-of-AND覆盖门控；只拒绝缺证，不能把语义拒绝提升为通过。未配置的候选标记reference_status=not_configured。该金标参考模式的结果与盲语义结果分别报告。

统一金标1、2、3、5（忽略4）的107组主对照：84→102组与原标注一致；30组删必需证据压力样本，语义模式拒绝20组，显式金标约束拒绝30组。剩余原标注冲突和全部细节见 `reports/evaluator_gold_2026-11-12/REPORT.md`。


### 2026-11-13 精标证据校准

Validator 与独立 Evaluator 共享 `judgment_policy.py` 的科学计量范围和充分路径，但各自独立判定。`use_path_review.py` 对未识别到实际应用路径的 `applies` 进行原文复核，并保留初判；不读取人工正确标签或另一裁判的结论。基础文献计数、技术距离、主题分布/增长按当前抽取规则纳入；HDI、模型内部量和普通内容编码不纳入。组成语句按输入→输出核对，禁止反向通过。

金标1、2、3、5、6的240组证据：两者各通过239组；唯一未通过为用户同意单列的Usage count证据只报告citation的冲突。按外层OR，65条关系全部通过。46项单元测试及13个开发负例（两者26/26拒绝）通过。开发校准报告及逐组结果见 `reports/gold_judge_calibration_2026-11-13/REPORT.md`；本结果不是未见样本准确率，release的完全相同请求复用来源均已记录。


### 2026-11-13 纯提示词裁判（当前版本）

当前Validator与Evaluator改为`semantic_prompts.py` + `semantic_judgment.py`：语义、类型、身份、方向与充分路径全部交给模型，不再按指标名称或正则句式改判；代码只验证JSON协议和证据ID。之前的精标校准数字属于旧混合版本，不能代表当前纯提示词通过率。26项离线测试通过；已获用户授权并完成模型回归：两者239/239组、65/65关系通过。Validator的3组由模型缺口复核通过，Evaluator均首判通过；不再使用名称/句式程序改判。40项开发边界判断正确。严格相同API请求的复用来源与逐项模型结论一致性均在报告中记录，非独立重复采样。见`reports/prompt_only_judges_2026-11-13/REPORT.md`。


## 2026-11-13 全称与确定性排版

实体优先原文明示全称，缩写、符号和原文写法保存在aliases；没有可靠全称绑定的数学指标保留其符号。entity_names.py统一数学字体/美元定界符、简单下标大括号、Unicode下标和排版连字符，保留字母大小写、下标内容与版本。不会全转小写或删除下划线；不会按编辑距离合并指标。模型误把列表符号写成聚合符号别名时，程序阻止该符号绑定。含糊别名有多个目标时不自动绑定。

别名中的完整名称只有在当前原文中实际出现且不与多个实体争用时才可提升为规范名；原文明示的-index正式名称不强行展开。实体清单在关系请求前规范化，关系/属性按同一清单绑定，证据原文保持不变。上述格式整理是确定性的，模型是否找到同一语义名称仍须靠原文身份绑定；跨论文、跨运行的永久名称统一需要维护经审核的实体词表，不能靠把大小写、下标全删掉实现。

40项离线测试覆盖身份层级、别名歧义、全称原文绑定、规范化幂等、完整抽取合并Schema及现有证据/裁判门控。线上开发回归和全部版本记录见reports/extraction_prompt_upgrade_2026-11-13/，结果以该目录最终报告为准，不以单元测试代替金标召回。

历史增强版还试验过全文证据回退和文档级身份锁定；这两项已退出当前流程。当前仍做实体名称和别名规范化。

本轮65条回归的执行入口为`experiments/full_first_paper.py`，批量入口`batch_extract.py`调用同一实现；使用全新输出目录。基础CLI `extract_md_deepseek.py`共享增强抽取与命名，但其证据调度接口不同，未将此回归结果当作基础CLI独立运行的效果保证。完整复跑示例：

```bash
.venv/bin/python extraction/experiments/full_first_paper.py \
  --markdown /absolute/path/full.md --output reports/your_new_run \
  --backend deepseek --model deepseek-flash --workers 2 --repair-attempts 2
```
