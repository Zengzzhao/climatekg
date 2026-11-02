# ClimateKG

ClimateKG 是一个面向气候科学论文的本体引导知识图谱构建项目，论文成果发表于 AACL 2026。项目先从 ESGF 数据集元数据、CMIP6 受控词表、GCMD 关键词和 GeoNames 构建可信的图谱基础，再从科学论文中抽取实体、关系和证据，并经过本体检查、证据核验、LLM 验证和实体对齐后写入图谱。

本项目发布版本包含论文中的 2,000 篇论文：

| 指标 | 数量 |
| --- | ---: |
| KG 节点 | 133,990 |
| KG 关系 | 755,308 |
| 抽取候选三元组 | 39,964 |
| 验证后保留三元组 | 23,327（人工判断正确率 98.96%） |

## 项目结构

```text
extraction/            论文信息抽取与验证流程
  experiments/         论文实验、消融、召回率和基线比较脚本
kg_create/             图谱基础数据构建和 Neo4j 导入
  gcmd/ geolocation/   GCMD 关键词和 GeoNames 数据
  other_sources/       CMIP6 source_id 受控词表
  output/              本体 Turtle 文件和 CMIP6 模型组件
data/                  2,000 篇论文的发布数据（压缩文件）
scripts/unpack_data.sh 将发布数据解压到脚本所需目录
```

## 数据

| 文件 | 内容 |
| --- | --- |
| `data/kg/nodes_20260622.cypher.gz`、`edges_20260622.cypher.gz` | 可直接交给 `kg_create/upload_neo4j.py` 的 Cypher 创建语句 |
| `data/kg/nodes_20260622.json.gz`、`edges_20260622.json.gz` | JSON 格式的节点和边 |
| `data/pipeline_outputs/<stage>_<date>.tar.gz` | 各阶段的逐论文 JSON 输出 |
| `data/pipeline_outputs/entity_linked_papers_2000.json.gz` | 实体对齐结果，也是图谱构建的输入 |
| `data/papers/candidate_papers_<date>.json`、`paper_metadata_v2.json` | OpenAlex 论文元数据 |

语料分为两个批次：`2026-02-01`（1,007 篇）和 `2026-04-24`（993 篇）。各阶段输出是累积的，每个三元组会保留所有阶段的判定结果。

| 阶段 | 输出目录 | 添加字段 |
| --- | --- | --- |
| 三元组和证据抽取 | `extracted_papers` | — |
| 本体一致性检查 | `ontology_conformed_papers` | `ontology_conformed` |
| 证据核验 | `evidence_validated_papers` | `evidence_validated` |
| Validator Agent | `validated_papers` | `valid` |
| C_triplet 评估（仅用于评测） | `evaluated_papers` | `eval` |

完整 Cypher 文件包含 826,139 条边语句，其中 70,829 条引用了当前元数据快照中不存在的 ESGF `Source` 节点，Neo4j 加载时会跳过这些边，因此最终图谱包含约 755,000 条关系。发布包不包含论文 PDF、解析后的全文和人工标注的 1,000 条一致性评估三元组。

图谱也发布在 Hugging Face：`v1` 是本仓库的 2,000 篇论文版本，`v2` 是使用相同流程构建的 10,910 篇论文扩展版本。

```python
from datasets import load_dataset
nodes = load_dataset("aayushacharya/climatekg", "v1-nodes", split="train")
edges = load_dataset("aayushacharya/climatekg", "v1-edges", split="train")
```

解压发布数据：

```bash
./scripts/unpack_data.sh
```

## 环境配置

项目原始气候论文流程使用 Python 3.10：

```bash
conda create -n climatekg python=3.10
conda activate climatekg
pip install -r requirements.txt
cp extraction/.env.example extraction/.env
```

如果已经创建了 venv，直接激活已有环境并执行：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

`.env` 中可配置 `GOOGLE_API_KEY`、`OPENAI_API_KEY`、`HF_TOKEN` 以及 Neo4j 连接信息。Neo4j 5 是图谱加载所需版本，默认地址为 `neo4j://localhost:8687`；也可以设置 `NEO4J_URI`、`NEO4J_USER` 和 `NEO4J_PASSWORD`。

## 将图谱加载到 Neo4j

`upload_neo4j.py` 会先删除目标数据库中的全部内容，再加载新的 Cypher 文件，因此只能连接空数据库或专门的重建数据库。

```bash
./scripts/unpack_data.sh
cd kg_create
python upload_neo4j.py --date 20260622
```

## 复现实验结果

阶段统计和消融结果直接读取已保存的阶段标志，不需要 API：

```bash
cd extraction
python experiments/stage_ablation.py
```

| 论文结果 | 脚本 |
| --- | --- |
| 阶段流失率和阶段消融 | `extraction/experiments/stage_ablation.py` |
| 分批次流水线统计 | `extraction/stats.py --date <date>` |
| CMIP 实体召回率 | `extraction/experiments/extract_cmip_publication_moreinfo_entities.py`、`evaluate_cmip_moreinfo_entity_extraction.py`、`analyze_cmip_missing_entities_in_parsed_papers.py`、`create_cmip_recall_report.py` |
| ClimateIE 比较 | `extraction/experiments/run_climateie_full_gemini_analysis.py` 及相关脚本 |
| KARMA 比较 | `extraction/experiments/karma/validate_1k_karma.py`、`analyze_1k_karma.py` |
| 证据位置和幻觉分析 | `extraction/experiments/evidence_section_analysis.py` |

部分基线实验需要额外数据，包括 ClimateIE 人工语料、KARMA 输出和解析后的论文全文。

## 原始气候论文流程

原始流程从 `extraction/` 目录运行。每个阶段读取前一阶段的 `output/<stage>/<date>/`，已经生成结果的论文会被跳过，因此中断后可以直接重启。

1. `find_papers.py` 通过 OpenAlex 查找候选论文，使用 Ollama 中的 Llama-3.3-70B 筛选并下载 PDF。
2. `parse_pdfs_olmocr.py` 使用 olmOCR 将 PDF 解析为 Markdown。
3. `extraction_agent.py` 使用 Gemini 抽取三元组、证据和论文级字段。
4. `ontology_conformance_agent.py` 检查实体类型、关系和方向是否符合 `consts.py` 中的本体。
5. `evidence_validation_agent.py` 将证据段落与解析后的论文文本进行模糊匹配。
6. `validation_agent.py` 使用 LLM 判断证据是否支持关系，默认模型为 Gemini-2.5-flash。
7. `evaluation_agent.py` 执行 C_triplet 评估，仅用于评测。
8. `create_embeddings.py` 使用 NV-Embed-v2 为图谱实体生成向量。
9. `entity_linking.py` 将抽取实体对齐到图谱节点，并对低置信度匹配使用 LLM 兜底。

随后在 `kg_create/` 中构建并加载图谱：

```bash
python download_esgf.py --help
python neo4j_v2.py
python upload_neo4j.py
```

## 使用 Markdown 和 DeepSeek 处理科学计量论文

当前新增入口直接读取你提供的 Markdown，不上传或解析 PDF，也不需要 olmOCR。它使用中文科学计量提示词，适配 KARMA 的指标抽取任务。

在 `extraction/.env` 中配置：

```dotenv
DEEPSEEK_API_KEY=你的 DeepSeek API 密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

运行：

```bash
cd extraction
python extract_md_deepseek.py \
  --markdown "/路径/论文.md" \
  --paper-id paper_001 \
  --date 2026-11-02
```

如果已有同名结果，需要加 `--force` 覆盖。当前抽取流程（已按需求移除原 Gemini 的筛选分支）：

1. 抽取论文书目信息：标题、作者、DOI、年份。
2. 直接抽取指标定义、公式和局限性；缺失内容返回空列表。
3. 独立抽取 `Indicator` 实体。
4. 独立抽取关系三元组和证据段落。
5. 合并结果，补全紧邻的公式证据，执行科学计量本体检查和 Markdown 证据核验。
6. 逐条调用 DeepSeek Validator，判断证据是否支持关系，添加 valid 标志。
7. 逐条调用独立 Evaluator，使用科学计量评估提示词重新判断，添加 eval 标志。

科学计量实体类型为 `Paper` 和 `Indicator`，关系包括 `proposes`、`modifies`、`applies`、`variant_of`、`derived_from`、`improves_on`、`alternative_to` 和 `component_of`。证据必须来自输入 Markdown；程序不会下载 Markdown 中的图片链接。

为避免证据停在 “The formula is shown below:” 却遗漏下一行公式，入口默认在抽取后执行
`local_formula_evidence.py` 的确定性边界补全：仅当证据以“公式如下/公式见下文”等提示结束、
且能在原文唯一精确定位时，追加紧邻的最多两块显示公式（`$$...$$`、`\[...\]` 或
math/latex 代码块）和一段以 where/with/in which/式中/其中开头的变量说明。
新增部分最多 2000 字符、40 个换行；不会跳过正文、标题或图片去寻找公式，也不按公式编号回查。
补全后的 `paragraph` 仍是连续原文，供证据核验、Validator 和 Evaluator 共同使用；
原证据保存在 `paragraph_original`，补全规则和字符偏移保存在 `local_formula_completion`。
该步骤不新增 LLM 请求，不更改关系标签，也不会自动把 valid/eval 设为 true。

这是 baseline 的局部证据完整性修复。添加 `--no-local-formula-completion` 可关闭该步骤，
复现旧版证据行为；建议新旧结果使用不同 `--output-root` 保存。既有 JSON 与实验报告不会自动改写，
需重新运行入口后才能得到基于新证据的 valid/eval 与 C_triplet。

当前 baseline 另包含以下提示词与模块修正，阶段顺序和四次抽取请求保持不变：

- 实体与关系仍独立抽取；统一指标命名规则，关系证据要求复制完整的单个自然段及紧邻公式。
  不按关系类型触发检索，不拼接不同章节，也不把指标细节字段当作额外关系证据。
- 抽取、Validator、Evaluator 共用明确的关系定义。局部 we/this study 可指当前论文，
  但不能把被引论文的贡献归为本文；复合指标的组成方向与方法衍生、相关与替代分别区分。
- 本体模块仅在名称经 Unicode、空白、连字符规范化后唯一匹配时修正排版差异，
  不用模糊相似度猜别名，不把缺失端点自动加入实体清单。对称关系规范方向；
  同篇论文提出并实际应用同一指标不再被错误视为互斥，variant_of/derived_from 仍互斥。
- DeepSeek 请求固定 temperature=0。保留 `validation_response`、`evaluation_response`；
  非 valid/invalid 的响应会报错，避免将格式错误混算为错误关系。

修改评估提示词后，应使用同一固定 Evaluator 重评新旧候选，再比较各阶段 C_triplet；
不要将旧裁判的低分直接与新裁判的高分视作抽取改进。小规模多轮实验及原始阶段文件位于
`reports/baseline_optimization_2026-11-02/`（第一版）与
`reports/baseline_optimization_v2_2026-11-02/`（当前第二版）。这些是五篇开发集上的结果，
不代表复现作者在2,000篇气候论文上的指标或人工一致性。
最终增加了组成关系反向/变体混淆的局部判定检查；固定最终裁判后，对第二版三轮全部候选
重新执行后续阶段，完整报告与最终代码快照位于
`reports/baseline_optimization_final_2026-11-02/`。这是固定候选重放，不是又一次整篇抽取，
也不是根据 eval 挑选关系；旧候选同样用最终裁判重新评估，并保留历史判定供比较。

后续针对前两阶段的优化保持 Validator/Evaluator、关系定义和所有评分代码不变，
重整了实体命名及单段原文支持的抽取提示词，并为本体名称匹配补充了数学定界符和简单下标括号的
排版等价规范化（例如 `$x_{d}$-index` 与 `x_d-index`）；下标值不同或匹配歧义仍不接受。
没有添加关系驱动检索、跨章节证据、候选预筛选或新的模型请求。
本轮三个配置各跑五篇三轮，最终保留第一版提示词与数学排版修正的组合；
额外长示例导致下降的第二版已撤回。全部结果、失败配置和固定裁判校验见
`reports/early_stage_optimization_final_2026-11-02/report.md`，
生产代码快照见同目录 `frozen_code/`。旧实验结果保留，不自动改写。

脚本默认关闭 DeepSeek 思考模式，把生成额度留给结构化 JSON；可以用 `--thinking enabled` 开启。`--max-tokens` 是每次模型请求的生成上限，默认值为 16384。若模型返回 `length`，程序会拒绝保存不完整结果。

输出文件为：

```text
output/extracted_papers/<date>/<paper-id>.json
output/ontology_conformed_papers/<date>/<paper-id>.json
output/evidence_validated_papers/<date>/<paper-id>.json
output/validated_papers/<date>/<paper-id>.json
output/evaluated_papers/<date>/<paper-id>.json
```

当前科学计量入口已经适配抽取、本体检查和证据核验。`validation_agent.py` 和 `evaluation_agent.py` 分别使用科学计量验证与独立评估提示词，不需要 `domain` 字段；实体对齐、向量生成和 Neo4j 构建仍是气候领域实现，尚未迁移到 `Paper`/`Indicator` 本体。

## 许可证

代码采用 MIT 许可证（`LICENSE`）。数据采用 CC BY 4.0（`DATA_LICENSE.md`）。

## 引用

```bibtex
@inproceedings{acharya2026climatekg,
  title     = {Ontology-Guided Knowledge Graph Construction from Structured Metadata and Climate Science Publications},
  author    = {Acharya, Aayush and Zhang, Qi and Dragut, Eduard and Latecki, Longin Jan},
  booktitle = {Proceedings of AACL 2026},
  year      = {2026}
}
```

科学计量输出不再包含 domain、extraction_provenance 或气候占位字段（model、obs_and_metrics、uri、venue、authors、abstract、keywords、concepts、referenced_works）。作者保存在 author_names。不再执行指标筛选，也不输出 indicator_screening 和 paper_screening；每篇论文固定执行四次抽取请求。本体批处理入口默认科学计量规则；如在 Python 中显式传入旧本体集合，仍可调用旧气候检查。Validator/Evaluator 直接使用科学计量提示词，不再自动切换到气候领域。已有 JSON 文件不会被自动改写，重新抽取时生成精简结构。

当前入口自动执行“抽取 → 本体检查 → 证据字符匹配 → LLM 语义验证 → 独立评估”。Validator
复用 validation_agent.py 的 validate_data/validate_triplet，默认使用抽取模型和同一 DeepSeek
配置，可用 --validator-model 指定另一个 DeepSeek 模型。每条三元组调用一次，包括本体或
证据检查未通过的项；所有判定分别保留，不以 valid 覆盖其他标志。Validator 只读取三元组
及 paragraph，不重新读取全文，也不验证 indicator_details 的数学正确性。原气候 Evidence
validator 也使用局部模糊字符匹配；新入口保留缺失 MD 报错和图表证据必须匹配的更严格规则。

Validator 失败时前三阶段文件仍保留，第四阶段不会写入不完整结果；可单独运行
`python validation_agent.py --date <date> --model deepseek-flash` 继续验证已有证据阶段结果，
已有验证结果需加 --force。完整入口的覆盖检查包含五个阶段。
Evaluator 使用 prompts_zh.py 的 EVALUATION_PROMPT，重新检查实体身份、关系方向、贡献归属、否定和适用条件。
它与 Validator 分别发起模型请求，输入不含先前 valid/ontology_conformed/evidence_validated 判定，
但默认使用同一模型；独立指请求与评估提示词独立，不代表使用不同模型或独立事实真值。
可用 --evaluator-model 指定其他 DeepSeek 模型。全部关系都会重新评估，包括 valid=false 的项，
旧标志保留，新增 eval=true/false。最终入图应结合所有判定，不能仅以 eval 代替 valid。
Evaluator 失败时前四阶段文件保留，第五阶段不会保存不完整结果。
可运行 `python evaluation_agent.py --date <date> --model deepseek-flash` 单独评估既有验证结果；
已有评估结果需加 --force。此流程仍不执行实体对齐或 Neo4j 入库。

## 表 3 同口径：C_triplet (%)

参考论文第 4.2 节及表 3，每行统计截至该阶段的累计保留三元组：

`C_triplet (%) = 100 × 该行保留结果中 eval=true 的条数 / 该行 Output Triplets`

四行依次为：全部抽取结果、ontology_conformed=true、再加 evidence_validated=true、
再加 valid=true。Evaluator 的 eval 是裁判标签，不参与生成这四行的筛选条件。
因此 Validator 行的分母不是全部候选，也不是 eval=true 的条数。
这是基于独立评估调用的 precision-like 指标，不是人工标注准确率或召回率；
若 Validator 和 Evaluator 使用同一模型，二者判断仍可能存在相关性。

完整入口在第五阶段评估完成后自动显示表格，并将 JSON、CSV、Markdown 导出到：

```text
output/reports/<date>/<paper-id>/table3.{json,csv,md}
```

对已有 Evaluator 结果可离线重算（无 API 调用）：

```bash
cd extraction
python triplet_metrics.py --dates 2026-11-02 --paper-id paper_001 \
  --report-dir output/reports/2026-11-02/paper_001

# 多批次合并：不传 --paper-id 则统计指定批次内所有论文
python triplet_metrics.py --dates 2026-11-02 2026-11-03 \
  --report-dir output/reports/combined
```

每个日期分区应仅保存该实验的一份最终结果；基线和实验副本应分开目录或使用
--paper-id，避免合并时重复计数。统计以抽取记录为单位，不做图边去重；跨论文汇总
先累加条数再除，不对论文百分比简单平均。缺失或非布尔阶段标志会报错；空集合
的百分比显示 N/A（JSON 为 null），不会把尚未评估的数据当成错误三元组。
