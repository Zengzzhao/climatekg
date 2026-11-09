"""Extract knowledge from supplied Markdown with DeepSeek and verify its evidence locally."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from typing import Literal

from models import Kg_Extraction_Model
from prompts_zh import (
    SYSTEM_INSTRUCTION,
    METADATA_EXTRACTION, INDICATOR_DETAILS_PROMPT,
    ENTITY_EXTRACTION_PROMPT, KG_LINKING_PROMPT,
)
from scientometrics_ontology import annotate_scientometrics
from evidence_validation_agent import partial_ratio_score, DEFAULT_THRESHOLD
from local_formula_evidence import complete_local_formula_evidence
from validation_agent import validate_data
from evaluation_agent import evaluate_data
from triplet_metrics import table3, markdown_table, write_report

SCRIPT_DIR = Path(__file__).resolve().parent


class IndicatorEntity(BaseModel):
    entity_instance: str
    entity_type: Literal["Indicator"]


class SupportedDetail(BaseModel):
    text: str
    evidence: str


class IndicatorDetails(BaseModel):
    indicator: str
    definitions: list[SupportedDetail]
    formulas: list[SupportedDetail]
    limitations: list[SupportedDetail]


class PaperInformation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    doi: str
    publication_year: int | None
    author_names: list[str]


class EntityExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities_extracted: list[IndicatorEntity]


class RelationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kg_extraction_triplets: list[Kg_Extraction_Model]


class DetailExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator_details: list[IndicatorDetails]


class Extraction(PaperInformation, EntityExtraction, RelationExtraction, DetailExtraction):
    """合并后的输出结构，保持现有下游字段兼容。"""


def request_stage(client, model, text, max_tokens, thinking, stage, schema, instructions, context=None):
    """每个任务使用独立请求与 Schema，共享同一份论文原文。"""
    print(f"正在抽取[{stage}]：model={model}, max_tokens={max_tokens}, "
          f"thinking={thinking}, 输入字符数={len(text)}", flush=True)
    stage_context = ""
    if context is not None:
        stage_context = ("当前论文元数据（仅用于论文身份约束，不能当作关系证据）：\n"
                         + json.dumps(context, ensure_ascii=False) + "\n\n")
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        extra_body={"thinking": {"type": thinking}},
        messages=[
            {"role": "system", "content": SYSTEM_INSTRUCTION
             + json.dumps(schema.model_json_schema(), ensure_ascii=False)
             + "\n本次仅执行以下任务，只返回本次 Schema 指定的字段：\n" + instructions},
            {"role": "user", "content": stage_context + "论文 Markdown 原文：\n" + text},
        ],
    )
    choice = response.choices[0]
    print(f"模型已返回[{stage}]：finish_reason={choice.finish_reason}, usage={response.usage}", flush=True)
    if choice.finish_reason != "stop":
        raise ValueError(
            f"Extraction incomplete: stage={stage}, finish_reason={choice.finish_reason}, "
            f"model={model}, max_tokens={max_tokens}. 输出未保存；请检查该阶段输出预算。"
        )
    return schema.model_validate_json(choice.message.content or "").model_dump()


def extract(client, model: str, text: str, max_tokens: int, thinking: str = "disabled",
            complete_formulas: bool = True) -> dict:
    """顺序抽取论文信息、指标细节、实体和关系证据，不进行筛选。"""
    def call(stage, schema, instructions, context=None):
        return request_stage(client, model, text, max_tokens, thinking,
                             stage, schema, instructions, context)

    metadata = call("论文信息", PaperInformation, METADATA_EXTRACTION)
    details = call("指标定义、公式与局限性", DetailExtraction,
        INDICATOR_DETAILS_PROMPT.replace("已抽取指标", "原文明确命名的指标")
        .replace("必须与实体清单一致", "使用原文名称")
        .replace("已抽取", "原文中的"))
    entities = call("实体", EntityExtraction, ENTITY_EXTRACTION_PROMPT)
    paper_name = metadata["title"] or metadata["doi"]
    paper_entity = ({"entity_instance": paper_name, "entity_type": "Paper"} if paper_name else None)
    # Like Gemini's KG call, this request does not consume the extracted entity list.
    context = {"title": metadata["title"], "doi": metadata["doi"], "paper_entity": paper_entity}
    relation_prompt = KG_LINKING_PROMPT.replace(
        "Indicator 端点必须与 entities_extracted 中的名称完全一致，实体清单本身不是关系证据。",
        "独立根据原文识别 Indicator 关系端点，使用原文名称；不要依赖另一轮实体抽取结果。")
    relation_prompt = relation_prompt.replace("以实体清单中靠前的指标为源", "以原文首次出现更早的指标为源")
    relations = call("关系与证据", RelationExtraction, relation_prompt, context)
    result = Extraction.model_validate({**metadata, **entities, **relations, **details}).model_dump()
    # Cross-stage endpoint checks belong to the subsequent conformance stage.
    result["paper_entity"] = paper_entity
    if complete_formulas:
        count = complete_local_formula_evidence(result, text)
        print(f"局部公式证据补全：{count} 条（仅紧邻原文）", flush=True)
    return result


def verify_evidence(data: dict, markdown: str, threshold: float) -> None:
    if not markdown.strip():
        raise ValueError("Markdown is empty; evidence cannot be verified.")
    for triplet in data["kg_extraction_triplets"]:
        paragraph = triplet.get("paragraph", "")
        triplet["evidence_validated"] = bool(
            paragraph.strip() and partial_ratio_score(paragraph, markdown) >= threshold
        )

    for detail in data.get("indicator_details", []):
        for kind in ("definitions", "formulas", "limitations"):
            for claim in detail[kind]:
                claim["evidence_validated"] = bool(
                    claim["text"].strip() and claim["evidence"].strip()
                    and claim["text"] in claim["evidence"] and claim["evidence"] in markdown
                )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    load_dotenv(SCRIPT_DIR / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--paper-id", help="Output ID; defaults to the Markdown filename stem")
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--model", default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--validator-model", help="Semantic validator model; defaults to the extraction model")
    parser.add_argument("--evaluator-model", help="Independent evaluator model; defaults to the extraction model")
    parser.add_argument("--base-url", default=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--output-root", type=Path, default=SCRIPT_DIR / "output")
    parser.add_argument("--max-tokens", type=int, default=16384)
    parser.add_argument("--thinking", choices=("disabled", "enabled"), default="disabled",
                        help="DeepSeek thinking mode; disabled keeps the budget for extraction output")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--no-local-formula-completion", action="store_true",
                        help="Disable adjacent formula completion to reproduce the original baseline")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.threshold <= 100 or args.max_tokens <= 0:
        parser.error("threshold must be 0..100 and max-tokens must be positive")
    if Path(args.date).name != args.date or args.date in (".", ".."):
        parser.error("date must be a single directory name")
    paper_id = args.paper_id or args.markdown.stem
    if Path(paper_id).name != paper_id or paper_id in (".", "..") or not paper_id:
        parser.error("paper-id must be a nonempty filename without directory components")
    base_url = args.base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        parser.error("base-url must be an API base, e.g. https://api.deepseek.com, not /chat/completions")
    paths = [args.output_root / stage / args.date / (paper_id + ".json")
             for stage in ("extracted_papers", "ontology_conformed_papers", "evidence_validated_papers", "validated_papers", "evaluated_papers")]
    if not args.force and any(path.exists() for path in paths):
        parser.error("Output already exists; choose a new date or use --force to replace these stage outputs")
    try:
        markdown = args.markdown.expanduser().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        parser.error(f"Cannot read Markdown: {exc}")
    if not markdown.strip():
        parser.error("Markdown is empty")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        parser.error("Set DEEPSEEK_API_KEY in extraction/.env")
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        data = extract(client, args.model, markdown, args.max_tokens, args.thinking,
                       complete_formulas=not args.no_local_formula_completion)
    write_json(paths[0], data)
    annotate_scientometrics(data)
    write_json(paths[1], data)
    verify_evidence(data, markdown, args.threshold)
    write_json(paths[2], data)
    validator_model = args.validator_model or args.model
    print(f"正在进行语义验证：model={validator_model}，逐条检查全部关系", flush=True)
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        total, valid = validate_data(data, client, "deepseek", validator_model)
    write_json(paths[3], data)
    print(f"语义验证通过：{valid} / {total}", flush=True)
    evaluator_model = args.evaluator_model or args.model
    print(f"正在进行独立评估：model={evaluator_model}，逐条重新判断全部关系", flush=True)
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        eval_total, eval_valid = evaluate_data(data, client, "deepseek", evaluator_model)
    write_json(paths[4], data)
    print(f"独立评估通过：{eval_valid} / {eval_total}", flush=True)
    rows = table3(data["kg_extraction_triplets"])
    report_dir = args.output_root / "reports" / args.date / paper_id
    write_report(rows, report_dir, "table3", [paths[4]])
    print(markdown_table(rows), flush=True)
    print(f"C_triplet reports: {report_dir}", flush=True)
    for path in paths:
        print(path)
    print("Evidence passed:", sum(t["evidence_validated"] for t in data["kg_extraction_triplets"]),
          "/", len(data["kg_extraction_triplets"]))


if __name__ == "__main__":
    main()
