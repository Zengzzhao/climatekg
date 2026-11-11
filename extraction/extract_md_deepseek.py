"""Extract knowledge from supplied Markdown with DeepSeek and verify its evidence locally."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from prompts_zh import (
    SYSTEM_INSTRUCTION,
    METADATA_EXTRACTION, INDICATOR_DETAILS_PROMPT,
    ENTITY_EXTRACTION_PROMPT, KG_LINKING_PROMPT, CANDIDATE_RELATIONS_PROMPT,
)
from scientometrics_ontology import annotate_scientometrics
from evaluation_agent import evaluate_data
from relation_evidence import collect_data
from evidence_schema import verify_units
from triplet_metrics import table3, markdown_table, write_report

SCRIPT_DIR = Path(__file__).resolve().parent


class IndicatorEntity(BaseModel):
    entity_instance: str
    entity_type: Literal["Indicator"]


class SupportedDetail(BaseModel):
    text: str
    evidence: list[str]


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


class RelationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_entity_instance: str
    source_entity_type: Literal["Paper", "Indicator"]
    relation: Literal["proposes", "modifies", "applies", "variant_of", "derived_from", "improves_on", "alternative_to", "component_of"]
    target_entity_instance: str
    target_entity_type: Literal["Indicator"]


class RelationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kg_extraction_triplets: list[RelationCandidate]


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    section: str
    start: int
    end: int


class ScientificTriplet(RelationCandidate):
    model_config = ConfigDict(extra="allow")
    evidence: list[EvidenceSpan] = Field(default_factory=list)


class DetailExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    indicator_details: list[IndicatorDetails]


class Extraction(PaperInformation, EntityExtraction, RelationExtraction, DetailExtraction):
    """Merged output with array evidence; downstream flags are retained."""
    kg_extraction_triplets: list[ScientificTriplet]


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
            ) -> dict:
    """Metadata -> entities -> entity-constrained candidates -> indicator details."""
    def call(stage, schema, instructions, context=None):
        return request_stage(client, model, text, max_tokens, thinking,
                             stage, schema, instructions, context)

    metadata = call("论文信息", PaperInformation, METADATA_EXTRACTION)
    entities = call("实体", EntityExtraction, ENTITY_EXTRACTION_PROMPT)
    paper_name = metadata["title"] or metadata["doi"]
    paper_entity = ({"entity_instance": paper_name, "entity_type": "Paper"} if paper_name else None)
    context = {"title": metadata["title"], "doi": metadata["doi"], "paper_entity": paper_entity,
               "entities_extracted": entities["entities_extracted"]}
    relations = call("关系候选（尚无证据）", RelationExtraction, CANDIDATE_RELATIONS_PROMPT, context)
    detail_prompt = (INDICATOR_DETAILS_PROMPT.replace("已抽取指标", "实体清单中的指标")
                    + "\nevidence 是原文字符串数组，每段一个元素。")
    details = call("指标定义、公式与局限性", DetailExtraction, detail_prompt, context)
    result = Extraction.model_validate({**metadata, **entities, **relations, **details}).model_dump()
    result["paper_entity"] = paper_entity

    return result


def verify_evidence(data: dict, markdown: str) -> None:
    if not markdown.strip():
        raise ValueError("Markdown is empty; evidence cannot be verified.")
    for triplet in data["kg_extraction_triplets"]:
        triplet["evidence_validated"] = verify_units(triplet, markdown)
    for detail in data.get("indicator_details", []):
        for kind in ("definitions", "formulas", "limitations"):
            for claim in detail[kind]:
                evidence = claim["evidence"]
                claim["evidence_validated"] = bool(claim["text"].strip() and evidence and all(e in markdown for e in evidence)
                    and any(claim["text"] in e for e in evidence))


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
    parser.add_argument("--relation-evidence", action=argparse.BooleanOptionalAction, default=True,
                        help="Use joint model evidence selection (default); --no-relation-evidence uses Top-k ablation")
    parser.add_argument("--evidence-rounds", type=int, default=1, choices=(0, 1))
    parser.add_argument("--experimental-evidence-bge", type=Path,
                        help="Opt in to relation-chain-350 for improves_on and alternative_to using local BGE files")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.max_tokens <= 0:
        parser.error("max-tokens must be positive")
    if args.experimental_evidence_bge and not args.relation_evidence:
        parser.error("Experimental evidence retrieval requires joint evidence selection")
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
        data = extract(client, args.model, markdown, args.max_tokens, args.thinking)
    write_json(paths[0], data)
    annotate_scientometrics(data)
    write_json(paths[1], data)
    validator_model = args.validator_model or args.model
    print(f"关系证据检索与验证：model={validator_model}", flush=True)
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        total, valid = collect_data(data, markdown, client, validator_model,
            method="coherent" if args.relation_evidence else "topk", max_rounds=args.evidence_rounds,
            experimental_bge=args.experimental_evidence_bge)
    verify_evidence(data, markdown)
    write_json(paths[2], data)
    write_json(paths[3], data)
    print(f"语义验证通过：{sum(t['valid'] for t in data['kg_extraction_triplets'])} / {total}", flush=True)
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
