# 科学计量关系证据实验交接

2026-11-09，Asia/Shanghai。工作区 `/Users/zengzizhao/Desktop/climatekg`，分支 `feature/exp1`，交接前 HEAD 为 `2811661`、工作树干净。`reports/` 被 Git 忽略，下面的结果都在本机，换机器或检出其他工作树时需要确认产物是否可见。

## 研究目标与接续边界

用户在做科学计量全文的指标实体、多标签关系和证据抽取。论文工作点 1 正在聚焦一个可验证的问题：在固定分片和字符预算内，能否为一条关系找齐一条证据路径，并保证跨段证据指向同一指标、版本、任务和归属。用户希望先用实跑结果判断哪一环值得优化，答辩时要能说明与标准方法的差别。回答尽量简洁，引用原文时保留原语言。模型提示词自然语言用中文，标签、字段、原文和必要检索词保留指定形式。

接手先读本文件和下方报告，按用户下一条要求行动。不要自动重跑实验、扩到批量或覆盖历史结果。已有 DeepSeek 授权限于各自实验：第一篇全文及分片的单篇全流程，以及第二篇标题、两条固定 `applies` 候选和各组最多 12 个分片的 12 次对照，均已完成。服务是 `api.deepseek.com`，模型 `deepseek-flash`。后续外发以实际授权范围为准。不要发送标注文件、翻译、推理链或凭证，不从 `.env` 输出密钥。

## 当前流程及代码

- 新流程仍是四次全文请求：元数据 → 指标实体 → 基于实体清单的关系候选 → 指标详情。详情不作为关系检索的输入。当前单篇和批量入口已移除早期逐对候选复核及同句改进触发。旧聊天流程图仍有这些节点，不能照搬。[新流程单篇入口](/Users/zengzizhao/Desktop/climatekg/extraction/experiments/full_first_paper.py) · [全文抽取](/Users/zengzizhao/Desktop/climatekg/extraction/extract_md_deepseek.py:126)
- 通过本体的每条候选独立排序。空行切块，长块 2,600 字符窗、2,000 字符步长。当前排序是词项 IDF、截断词频和长度归一化，加每个端点命中 2 分和特定章节 0.25 分。首轮取最多 12 个单元，**没有首轮字符上限**。[分片与排序](/Users/zengzizhao/Desktop/climatekg/extraction/evidence_index.py)
- 每条候选一次发出首轮全部分片，按该关系路径的角色并集标注。每片可有零个或多个角色。程序按句子编号取回原文，必要时补入先行句，并单独保留整单元名称命中的身份记录。`coherent` 模型读原文、路径和角色标注，选最多 6 个单元、14,000 原文字符。**角色覆盖率是选证之后的诊断量**。它不是程序约束，也不拦截 Validator。[证据标注与补检](/Users/zengzizhao/Desktop/climatekg/extraction/relation_evidence.py) · [角色与路径原文](/Users/zengzizhao/Desktop/climatekg/extraction/evidence_selection.py:8)
- 原文数组逐字核验后送 Validator。默认允许一次定向补检。互斥在验证后处理，`variant_of` 与 `derived_from` 仅在同一有向对双双满足保留条件时才冲突。Evaluator 独立评估，不参与补检、选证或最终保留。保留规则为 `ontology_conformed AND evidence_validated AND valid`。`C_triplet` 是集合内 Evaluator 认可的比例，不能充当人工准确率或全文召回率。
- 当前 `cover` 是 `coherent` 的兼容别名。历史三组对比的 `cover` 是冻结的贪心覆盖实现。严格旧流程单独请求实体，关系请求独立识别端点并同时输出 `paragraph`，后续才核对实体清单。当前新旧实体任务提示词共用，关系任务提示词和证据 Schema 不同。[严格旧流程入口](/Users/zengzizhao/Desktop/climatekg/extraction/experiments/migrated_baseline/run.py) · [当前提示词](/Users/zengzizhao/Desktop/climatekg/extraction/prompts_zh.py)

## 第一篇全流程实跑

目录 `reports/paper_001_coherent_trace_2026-11-09_Gf0dI6/`。交接时其中冻结的 Python 源码逐文件与当前版本一致。`deepseek-flash` 从全文抽得 **125 个分片、5 个指标、14 条直接候选、11 条最终保留**，保留的 11 条均获本轮 Evaluator 认可。未对同一语义判定重复采样。

- [全部分片及原文偏移](/Users/zengzizhao/Desktop/climatekg/reports/paper_001_coherent_trace_2026-11-09_Gf0dI6/evidence_units.md)
- [五步示例与输出字段](/Users/zengzizhao/Desktop/climatekg/reports/paper_001_coherent_trace_2026-11-09_Gf0dI6/walkthrough.md)
- [14 条候选、16 轮证据处理的紧凑记录](/Users/zengzizhao/Desktop/climatekg/reports/paper_001_coherent_trace_2026-11-09_Gf0dI6/evidence_trace.json)

示例 `x_d(FN)-index variant_of x_d-index` 首轮模型返回 9 条角色标注。`e0005` 的变体主张从 `[9]` 回查扩到 `[7,8,9]`。最终选 `e0005/e0027/e0039/e0062`，逐字核验通过，变体声明路径覆盖 3/3，Validator 首轮通过。

运行中 `c008/c012/c013` 的补检返回了短名单外 ID。原始响应保留，只有本次运行用 `resume_with_id_repair.py` 丢掉非法 ID 后续跑，未改主代码。`c008` 无合法新增分片，保留首轮 Validator 判定。不要将此异常恢复描述为主流程自带行为。`summary.json` 的耗时字段只统计最后一次恢复调用。

## 标注和检索对照

源标注 [实验数据.md](</Users/zengzizhao/Desktop/实验数据.md>)。两篇论文是 `x_d-index and its variants` 与 `A deep learning-based method for predicting the emerging degree of research topics using emerging index`。实际全文路径与哈希见结果 JSON 的 `manifest.documents`。交接时标注和两篇全文均与实验哈希匹配。

共有 5 条候选和 9 段证据。主表用显式 `valid` 的 q1、q2（两个变体，共用摘要段）、q4、q5（`Novelty`、`PopularityScore` 的两条 `applies`，共用验证段），共 4 条候选、6 段证据。q3 `xx_d-index derived_from x_d-index` 未写 `judgment`，3 段只作诊断。7 段归一化后精确对齐，另 2 段实际是同一摘要引文，归一后的对齐相似度为 0.997967。不要把这批小样本视为独立测试集、穷尽证据集或金标准。

固定关系候选。先把标注对齐到全文字符区间，再按检索单元的区间并集算覆盖。单段覆盖至少 90% 记一次命中。每格是“命中段数 / 全部标注段均命中的关系数”。额外检查前 12 条限 14,000 字符，表中各行结论相同。该字符上限是实验控制。

| 原文切分与排序 | 标注段命中 | 完整命中关系 |
|---|---:|---:|
| 现有切分 + 现有稀疏 | 2/6 | 2/4 |
| 现有切分 + BM25，同一查询 | 2/6 | 2/4 |
| 现有切分 + E5-small-v2，角色查询或自然查询 | 0/6 | 0/4 |
| 现有切分 + BM25 与 E5 混合，自然查询 | 2/6 | 2/4 |
| 优先句界 + 现有稀疏 | 2/6 | 2/4 |
| 句界与公式邻文 + 现有稀疏 | 3/6 | 2/4 |
| 现有切分与排序 + 章节引用补齐 | 5/6 | 3/4 |

解释边界：E5 对照使用固定公开权重、余弦相似度、最多 512 token，未针对本域训练。两个变体查询都把含 `variants` 的小节标题排在第一，但漏标注摘要，只说明这批样本上的相关性失配。学习排序需要独立训练数据和困难负例。章节引用补齐按开发案例设计，在 `applies` 的高排名锚点中跟随原文明确写出的章节标题，补章节开头及必要的邻接公式。5/6 是开发数字，两个验证段命中是同一原文位置。`PopularityScore` 的构造段仍未找齐。

- [检索实验完整报告](/Users/zengzizhao/Desktop/climatekg/reports/retrieval_pilot_2026-11-09_npPA8Y/report.md)
- [150 条检索对照、候选排名与原文对齐](/Users/zengzizhao/Desktop/climatekg/reports/retrieval_pilot_2026-11-09_npPA8Y/retrieval_results_with_query_control.json)
- [离线实验脚本](/Users/zengzizhao/Desktop/climatekg/reports/retrieval_pilot_2026-11-09_npPA8Y/pilot.py) · [模型首轮对照输出](/Users/zengzizhao/Desktop/climatekg/reports/retrieval_pilot_2026-11-09_npPA8Y/selection_probe.json)

## 最关键的观察与下一步

第二篇做过 **2 条固定候选 × 2 种检索池 × 3 个模型阶段 = 12 次 DeepSeek 调用**。原切分、相同标注和选证提示、相同 Validator，关闭补检。章节引用版让效果验证段在两个池中都达到 **99.7%** 覆盖，但角色标注没有登记其支持，`coherent` 最终都没选中。四组路径覆盖率都是 1.0，Validator 都为 `valid`。`PopularityScore` 的构造段在引用版的最终选证甚至从 56.4% 降到 31.5%。这说明现有 `actual_use` 可以由计算叙述满足，**召回证据与保住完整论证是两个阶段**。原标注以外的证据也可能足够，需人工判断，不能仅凭没选标注段就定性为关系错误。

工作点 1 的可检验版本是**按关系定义分解可替代路径，在预算内选择条件齐全且绑定一致的一条路径，按实际缺口补检**。可以先用 `applies` 打样。直接路径用明确的实际使用陈述。组合路径用“目标指标进入计算”加“同一计算确实用于实证”，绑定同一公式、指标版本和任务。将路径与绑定从模型参考信息变成可关闭的程序规则。这个改法尚未实现或验证，不能写成已有创新成果。

若用户让继续实验，优先固定候选、检索池和预算比较 Top-k、直接 LLM 选证、当前 `coherent`、显式路径、显式路径加绑定。先用这两篇开发，再按论文分组留独立样本，标注可替代证据集、角色与困难负例。测完整证据集保留率、跨段绑定错误、字符或调用成本，关系 P/R/F1 要等人工标注覆盖正负例后再报。另做固定选证器的检索消融，避免把两阶段收益混为一谈。可参考报告中的 BM25、E5、BEIR、HoVer 和 xQuAD 原始来源，正式论文引用前重新核对。
