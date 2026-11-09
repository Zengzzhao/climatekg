# ClimateKG

ClimateKG 是一个面向气候科学论文的本体引导知识图谱项目。AACL 2026 发布版以 ESGF 数据集元数据、CMIP6 受控词表、GCMD 关键词和 GeoNames 为基础，将论文中经过本体检查、证据核验、LLM 验证和实体对齐的关系写入图谱。仓库也提供独立的科学计量抽取流程，从 Markdown 论文中抽取指标、关系及其原文证据。

## 目录

| 路径 | 用途 |
| --- | --- |
| [`data_get/`](data_get/) | 用 MinerU 将本地 PDF 转成 Markdown 和配图，核验转换结果 |
| [`extraction/`](extraction/) | 科学计量抽取、证据检索、Validator、Evaluator 和批量调度 |
| [`extraction/experiments/migrated_baseline/`](extraction/experiments/migrated_baseline/) | 使用冻结源码和当前提示词的科学计量旧流程对照 |
| [`extraction/experiments/climate_baseline/`](extraction/experiments/climate_baseline/) | 原气候论文抽取、证据核验和实体对齐工具 |
| [`kg_create/`](kg_create/) | 气候图谱构建、Neo4j 导入和 RDF 导出 |
| [`data/`](data/) | 发布版图谱、逐论文流水线结果和论文元数据 |
| [`scripts/`](scripts/) | 解压发布数据和构建 Hugging Face 数据集 |

## 环境配置

从仓库根目录执行。项目依赖以 Python 3.10 为基准。已有 `.venv` 时可直接激活。

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

首次配置科学计量流程时，将 `extraction/.env.example` 复制为 `extraction/.env`，填写 `DEEPSEEK_API_KEY`，并按服务配置 `DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。基址填到 API 根路径。`python extraction/check_api.py` 会发送两条请求，检查 JSON 抽取和二元标签接口。CUC 校园服务配置见[抽取工程说明](extraction/README.md#cuc模型api接入)。

## PDF 转 Markdown

MinerU 读取 `MINERU_TOKEN`、`MINERU_API_TOKEN` 或 `data_get/.mineru-token`。先加 `--dry-run` 确认输入范围，再去掉该参数执行。脚本按论文保存 Markdown 和配图，在输出根目录的 `.mineru/` 下记录进度。

```bash
bash data_get/run_mineru.sh \
  --input /path/to/pdfs --output /path/to/markdown \
  --year 2024 --limit 3 --dry-run
```

对已完成的年份，可运行下方命令核对 PDF、Markdown 和图片引用。更多选项见 [MinerU 使用说明](data_get/README.md)。

```bash
python data_get/verify_mineru_output.py \
  --input /path/to/pdfs --output /path/to/markdown --year 2024
```

## 科学计量抽取

### 单篇

```bash
python extraction/extract_md_deepseek.py \
  --markdown /path/to/paper/full.md \
  --paper-id paper_001 --output-root reports/scientometrics_single
```

主入口从同一份论文全文发起四次抽取请求，依次取得论文元数据、`Indicator` 实体、受实体清单约束的关系候选，以及指标的定义、公式和局限。关系端点支持 `Paper → Indicator` 和 `Indicator → Indicator`，关系类型见 [中文提示词](extraction/prompts_zh.py)。

1. 本体检查核对候选的端点、关系类型和方向。
2. 系统按候选关系检索 12 个原文单元，标注证据角色，再由模型联合选择原文片段。每条关系最多选择 6 段、14,000 个原文字符。角色覆盖用于诊断，证据能回指原文即可交给 Validator 核验。
3. Validator 返回 `verdict`、`reason`、`missing_evidence` 和 `search_queries`。首次拒绝后，系统最多根据反馈补检一次，并从合并后的池中重新选证。关系的 `evidence` 是对象数组，每项包含 `id`、`section`、`text`、`start`、`end`，其中 `text` 必须等于 `markdown[start:end]`。每轮检索、选证和验证记录在 `evidence_completion.trace` 中。
4. 关系保留要求 `ontology_conformed`、`evidence_validated`、`valid` 均为 `true`。同一有向指标对的 `variant_of` 和 `derived_from` 如果都通过证据核验和 Validator，两条均标记为本体冲突并排除。Evaluator 独立复评全部候选，`eval` 只用于统计。

单篇入口在 `<output-root>/<stage>/<date>/<paper-id>.json` 保存五个阶段的快照，并在 `<output-root>/reports/<date>/<paper-id>/` 生成 `table3.json`、`table3.csv` 和 `table3.md`。阶段目录依次为 `extracted_papers`、`ontology_conformed_papers`、`evidence_validated_papers`、`validated_papers` 和 `evaluated_papers`。默认使用当天日期和 Markdown 文件名。运行时可用 `--no-relation-evidence` 切换到 Top-k 选证，或用 `--evidence-rounds 0` 关闭补检。`--validator-model` 和 `--evaluator-model` 可分别指定验证与评估模型。

### 批量与实验记录

批量入口扫描输入目录的直接子目录，每篇论文须包含 `full.md`。按目录名排序后逐篇启动独立子进程。先用 `--dry-run` 核对清单，去掉该参数即可运行。

```bash
python extraction/batch_extract.py \
  --input-root /path/to/markdown/2024 \
  --output reports/scientometrics_batch \
  --stop-after 5 --dry-run
```

`--stop-after` 保留全量清单，允许在同一批次扩大处理范围。已完成的论文会跳过，使用 `--retry-failed` 可为失败项新建尝试目录。输入、单篇代码、提示词或模型配置变化时使用新的输出目录。`--workers` 控制每篇内部验证和评估的并发数。

批次根目录保存 `manifest.json`、`batch_summary.json` 和 `report.md`。每篇的 `papers/<paper_id>/attempt_<编号>/` 保存阶段 JSON、`final_graph.json`、统计表、源码快照和 API 请求记录。若只运行一篇且需要相同的实验记录，可以直接使用 `extraction/experiments/full_first_paper.py`，传入 `--markdown` 和 `--output`。详细参数和输出见[抽取工程说明](extraction/README.md)。

`C_triplet` 按阶段计算，为该阶段剩余关系中 Evaluator 判为有效的比例。批次统计按关系条数汇总。该指标反映模型复评结果。

### 旧科学计量基线

旧基线隔离加载 `cb2e962` 的 8 个冻结模块，并注入当前中文提示词，其中 Validator 使用 `LEGACY_VALIDATION_PROMPT`。它保留字符串 `paragraph`、旧本体互斥和旧裁判调用方式。可先离线校验冻结源码，再用 `--markdown` 和 `--output` 启动单篇对照。批量对照使用该目录的 `batch.py`，另设输出目录。参见[旧基线说明](extraction/experiments/migrated_baseline/README.md)。

```bash
python -m extraction.experiments.migrated_baseline --check-only
```

## 气候图谱发布版

发布版覆盖 2,000 篇论文，分为 `2026-02-01`（1,007 篇）和 `2026-04-24`（993 篇）两个批次。论文报告 133,990 个图谱节点、755,308 条图谱关系，以及 39,964 条候选三元组中的 23,327 条保留关系。发布包包含逐论文判定和证据引文，论文 PDF 和解析后的全文需自行取得。

| 数据 | 位置 |
| --- | --- |
| 节点与关系的 JSON、Cypher 转储 | `data/kg/{nodes,edges}_20260622.{json,cypher}.gz` |
| 各阶段的逐论文 JSON 及实体对齐结果 | `data/pipeline_outputs/` |
| OpenAlex 论文元数据 | `data/papers/` |

### 解压与统计

```bash
./scripts/unpack_data.sh
python extraction/stats.py --date 2026-02-01
python extraction/stats.py --date 2026-04-24
python extraction/experiments/stage_ablation.py
```

解压脚本把阶段文件放入 `extraction/output/`、论文元数据放入 `extraction/papers/metadata/`，图谱转储放入 `kg_create/neo4j/`。阶段统计和消融直接读取发布的判定字段，可离线运行。原始气候抽取和实体对齐入口见[气候工具说明](extraction/experiments/climate_baseline/README.md)，KARMA 对比脚本位于 [`extraction/experiments/karma/`](extraction/experiments/karma/)。

### Neo4j 导入

`upload_neo4j.py` 会清空目标数据库中的节点和关系，再从 Cypher 文件重建。请使用空库或专门的重建库。脚本从环境读取 `NEO4J_URI`、`NEO4J_USER` 和 `NEO4J_PASSWORD`，默认地址为 `neo4j://localhost:8687`。解压后从 `kg_create/` 运行。

```bash
cd kg_create
python upload_neo4j.py --date 20260622
```

### Hugging Face

`v1` 对应 2,000 篇论文的发布版，`v2` 对应 10,910 篇论文的扩展版。字段、版本和导出说明见 [Hugging Face 数据集卡](huggingface/README.md)。

```python
from datasets import load_dataset

nodes = load_dataset("aayushacharya/climatekg", "v1-nodes", split="train")
edges = load_dataset("aayushacharya/climatekg", "v1-edges", split="train")
```

## 许可证与引用

代码采用 [MIT](LICENSE)。发布数据采用 [CC BY 4.0](DATA_LICENSE.md)，上游数据各自适用其许可条款。

```bibtex
@inproceedings{acharya2026climatekg,
  title     = {Ontology-Guided Knowledge Graph Construction from Structured Metadata and Climate Science Publications},
  author    = {Acharya, Aayush and Zhang, Qi and Dragut, Eduard and Latecki, Longin Jan},
  booktitle = {Proceedings of AACL 2026},
  year      = {2026}
}
```
