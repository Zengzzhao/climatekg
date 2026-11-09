# 保留的气候领域工具

此目录收纳从extraction根目录移入的遗留气候代码，避免与当前科学计量方法混放。它不是cb2e962科学计量迁移基线；后者位于同级migrated_baseline目录。

- extraction_agent.py：原Gemini气候论文抽取。
- models.py、consts.py、prompts_deprecated.py、utils_deprecated.py：对应Schema、本体、中文提示词及工具。
- ontology_conformance_agent.py、evidence_validation_agent.py：气候类型边界、证据定位核验。
- create_embeddings.py、entity_linking.py：图谱向量及实体对齐。
- find_papers.py、parse_pdfs_olmocr.py：论文查找与解析。

内部引用改为experiments.climate_baseline下的明确包路径。默认数据及配置保持原位：extraction/.env、extraction/output、extraction/papers、extraction/embedding_files。历史数据未移动。

从仓库根目录运行：

```bash
.venv/bin/python extraction/experiments/climate_baseline/extraction_agent.py --help
.venv/bin/python extraction/experiments/climate_baseline/ontology_conformance_agent.py --help
```

根目录validation_agent.py和evaluation_agent.py服务于科学计量新方法，不能直接当作气候本体裁判；这个原有领域分流问题没有因目录整理自动解决。KARMA保留的气候提示词从本目录prompts_deprecated.py导入，不改变其原评测语义。

工程整理将PyPDF2改为仅在check_pdf函数被调用时导入，避免无关的抽取CLI帮助被可选PDF健康检查依赖阻塞。当前环境PyPDF2触发的系统pyexpat／libexpat符号兼容问题仍可能影响该健康检查函数，未修改系统库。
