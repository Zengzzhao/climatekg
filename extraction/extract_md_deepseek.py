"""Extract knowledge from supplied Markdown with DeepSeek and verify its evidence locally."""
import argparse
import copy
import datetime as dt
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from llm_backend import add_backend_argument, configure_backend, openai_client as OpenAI
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from prompts_zh import (
    SYSTEM_INSTRUCTION,
    METADATA_EXTRACTION, NAME_CONVENTION, relation_task_prompt,
)
from scientometrics_ontology import annotate_scientometrics
from evaluation_agent import evaluate_data
from relation_evidence import collect_data
from evidence_schema import match_units
from triplet_metrics import table3, markdown_table, write_report
from entity_names import normalize_inventory, canonical_spelling

SCRIPT_DIR = Path(__file__).resolve().parent


class IndicatorEntity(BaseModel):
    entity_instance: str
    entity_type: Literal["Indicator"]
    aliases: list[str] = Field(default_factory=list)


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


class ParagraphRelation(RelationCandidate):
    paragraph: str = Field(min_length=1)


class ParagraphRelationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kg_extraction_triplets: list[ParagraphRelation]


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


class PaperInventory(PaperInformation, EntityExtraction, DetailExtraction):
    """First request jointly identifies the paper, indicators and their properties."""


class Extraction(PaperInformation, EntityExtraction, RelationExtraction, DetailExtraction):
    """Merged output with array evidence; downstream flags are retained."""
    kg_extraction_triplets: list[ScientificTriplet]
    entity_name_normalization: list[dict] = Field(default_factory=list)


def request_stage(client, model, text, max_tokens, thinking, stage, schema, instructions, context=None):
    """每个任务使用独立请求与 Schema，共享同一份论文原文。"""
    print(f"正在抽取[{stage}]：model={model}, max_tokens={max_tokens}, "
          f"thinking={thinking}, 输入字符数={len(text)}", flush=True)
    stage_context = ""
    if context is not None:
        stage_context = ("当前论文信息、实体与属性（仅用于身份约束和原文定位，不能代替原文关系证据）：\n"
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


RELATION_LABELS = (
    'proposes', 'modifies', 'applies', 'variant_of', 'derived_from',
    'improves_on', 'alternative_to', 'component_of',
)
PAPER_RELATIONS = frozenset(('proposes', 'modifies', 'applies'))


def extract_inventory(client, model: str, text: str, max_tokens: int,
                      thinking: str = "enabled") -> dict:
    """One shared metadata, indicator inventory and properties request."""
    inventory_prompt = (
        METADATA_EXTRACTION + r"""
PROJECT-SPECIFIC ENTITY ANNOTATION. Return the joint schema, not relations.
The annotation decisions below are authoritative. They override the paper calling something an indicator, metric, score, index, or quantitative result. A quantity can be scientifically useful and STILL be excluded from this project's ontology. Do NOT return excluded objects in either entities_extracted or indicator_details. Apply these decisions before filling properties.

OUT OF SCOPE (not aliases of valid entities either):
- Main-group patent share (P_g) and Subgroup patent share (p_i) are excluded calculation intermediates/reference shares, even when named, defined or used as formula inputs. Their mathematical/full-name variants remain excluded. Do not block every P_ symbol: independently defined admissible patent-novelty or technical-distance outputs must be judged by their own identity.
- Human Development Index / HDI is a general human-development socioeconomic index, never a scientometric Indicator in this project. Using it to explain publication citations or as a control variable does not change its identity.
- Share of matched keywords / percentage of matched keywords is a text/category matching statistic, not an admitted scholarly-impact or knowledge-diversity Indicator. Do not confuse it with a genuine publication/citation share or disciplinary diversity measure.
- P_link1, P_link2 and P_semantic (including mathematical subscript and full-name variants) are excluded internal linking/semantic calculation components. Exclude those specific objects, not every symbol beginning with P_. A separately defined patent-novelty output such as P_novelty or Score_novelty can still be eligible.
- Acceptance time / time to acceptance, Number of pages / page count, Title length are editorial-process durations or document descriptors, not admitted scientometric measures. They remain excluded when they are the paper's main topic, a dependent/control variable, or a significant predictor.
- Average delay year, Average intensity, Average leading year, and generic Duration and Frequency summary columns are excluded project objects. Match the generic Duration/Frequency object, NOT these words as substrings of valid measurement names. Preserve explicitly defined Frequency of research topics, frequency of each journal and co-occurrence frequency of journal pairs. Do not rename an excluded generic Frequency/Duration column to evade its exclusion, and do not use it as an alias of a different valid measure. A separately defined research-topic temporal period requires its own source identity; it cannot borrow a generic Duration column's identity.
- Topic-model quality/internal quantities: Coherence, Topic coherence, Perplexity, theta(θ), θ, document-topic probability. All document counts, percentages and bins defined by a theta threshold: %>0.50(%), Total docs >0.50, Total documents >0.50, and analogous thresholds.
- Ranking comparison/statistics: Average Overlap, AO, Time-Lagged Cross Correlation, TLCC. The hyperprolific author ranking is a resulting author ordering, NOT an Indicator node in this project.
- Graph representation: edge weight, number of edges, number of nodes, their symbols, yearly versions, full-network/backbone versions. Adding an object name or year does not turn these graph fields into admissible scientometric measures.
- Project-specific excluded background items: hI annual, hI norm, papers per year, generic coverage, web links. In web/website/link-visibility evaluation, Page Rank is excluded. In a different paper explicitly measuring publication citation impact, PageRank may be an admissible publication-impact measure; do not transfer web evidence to it.
- Model/algorithm performance or general statistical tools: Precision, Recall, F1, MAP, MRR, NDCG, MAE, RMSE, correlations, regressions, significance, fit, coding agreement, Benford tests/digit distributions, hyperparameters, internal vectors, algorithms or software names.
- Class 1/2/3 or named threshold classes; sample groups such as Fusions with cross-citing during a time period; sample-selection rows. Do not rename an excluded group by adding Number of.
- mean-field citations as a normalization reference, thematic-strength aggregation intermediates, general socioeconomic indices.

IN SCOPE: actual scientometric/bibliometric measurement of publication/patent production, citation impact, scholarly usage/readership/mentions, research-topic size/evolution, knowledge structure, scientific collaboration, and expressly quantified research-evaluation dimensions. Do not include an arbitrary quantity just because the article is scientometric.
Keep h-index, g-index, P-index, x-index and named genuine variants when introduced in the article body, including prior/background baselines. These are not hI annual or hI norm. Keep publication citation counts, reference counts, full-count citations, fractional/field-normalized publication citation scores. Independent author contributions/biases, topic measures, genuine centrality/collaboration measures and formal quantitative dimensions can qualify; distinguish them from the excluded ranking/graph fields and class labels.

COVERAGE REQUIREMENTS WITHIN THE ADMISSIBLE SCIENTOMETRIC DOMAIN:
Do not interpret the exclusions as "only composite indices qualify". The following independently named measurements are admissible when the article defines or identifies them; the explicit OUT OF SCOPE list still overrides this paragraph.
- Publication, patent, citation, reference, readership, download/usage and scholarly-event/mention counts; counts per author, journal, organization or country; totals, annual/monthly/early windows, fractions and publication-level/journal-level averages. Scan all explicitly identified scholarly-event platforms/fields, not just the few selected for the main analysis. A zero, unanalysed, or background-defined field can still be an entity.
- Background journal and citation indicators, including Journal Impact Factor, CiteScore and SCImago Journal Rank when present; all formally named index baselines and variants. Do not omit a genuine named baseline merely because it lacks its own equation in this paper.
- Research-topic frequency, Annual Growth Rate, Average Annual Growth Rate, topic prevalence and year-wise topic proportion, popularity, novelty, temporal age/duration and diversity. A topic's aggregate year-wise share is not the excluded document-topic theta or theta-threshold coverage. Preserve these different measurement identities rather than outputting a generic internal probability.
- Scientific collaboration/coauthorship counts, centrality, average degree, node assortativity, publication speed and acceleration and their explicitly defined averages/standard deviations, backbone hits/retention fraction/presence probability. These named research-object measures are different from generic graph node/edge counts/weights and from the excluded hyperprolific author ranking. Do not drop all network research measurements together with the excluded graph fields.
- Publication novelty and its defined journal-pair/co-occurrence/frequency measurements, individual/total/calibrated author bias, author-contribution measures, dataset production/citation/disciplinary-distribution measures, and patent knowledge novelty/distance/technical indicators. Check each equation's where-definitions for independently meaningful admissible measurements; do not include pure parameters or invent undefined ones.
- A formal research/innovative-university evaluation system's total index, quantified dimensions and explicitly listed subindicators of research production, research projects/resources, intellectual property, collaboration, research talent and research commercialization. Include each named measurement level; a quantified dimension aggregating subindicator values is not a threshold Class label. Keep the established measurement system, not arbitrary social/economic statistics or algorithm ranking flows.
Use full names without appending their symbols. Preserve list versus aggregate, original versus modified, and annual/monthly/early versus all-time identities. Build the complete inventory before extracting properties; absent properties do not disqualify an entity.

MANDATORY SOURCE-TO-ENTITY DECISIONS, only when supported by this document:
1. If publication citation counts or number of citations received are discussed, include the basic Indicator Citation count, even when it is merely an input to a composite index. A composite is not a substitute for its admissible basic input.
2. If f_t is defined as the frequency of research topics in year t, include Frequency of research topics. Keep f_t and its explicit source definition as aliases. Topic frequency is not a term weight or theta probability.
3. If the document states that annual usage data were obtained, or usage counts were calculated for each publication in each year, include Annual usage counts as a separate entity. Do NOT output only Usage counts. Similarly, explicitly annual readership/citation fields require Annual Mendeley readership and Annual citation counts. Explicit monthly and early windows are separate identities. A shortened name later in the article does not erase the original window.
4. If cit is explicitly defined as full count citations, include full-count citations and register cit as its alias. Do not confuse this with a field reference average or thematic-strength sum.
5. Background baselines, formula-defined list scores versus aggregates, original versus modified versions, and explicitly named quantitative dimensions must be checked separately. Similar or reused symbols are not permission to merge identities. Do not invent a dimension from a mere class label.

Scan introductions, definitions, every equation's where-clause, collected-data descriptions, text-table titles/headers/rows/notes, results and genuine captions. Bibliography entries, image filenames/alt text, and image contents are not measurement evidence. An uncomputed background index may still be an entity; a computed excluded tool is still excluded.

Before returning JSON, explicitly check the names against OUT OF SCOPE and remove excluded objects from your proposed answer. Then recheck the document for the four basic positives h-index, Citation count, Frequency of research topics and Annual usage counts; include each supported one, never fabricate one in a document that lacks it.
""" + NAME_CONVENTION + r"""
Output only title, doi, publication_year, author_names, entities_extracted, indicator_details. Each entity has entity_instance, entity_type=Indicator, aliases (at most 4 explicitly bound names). Reuse the exact canonical name in details. Prefer full measurement names without appending symbols; put symbols/abbreviations in aliases. Preserve versions, windows and levels. Extract properties only after the complete admissible inventory; at most 2 short verbatim items per definitions/formulas/limitations, missing lists []. Each property has text and evidence as an array of verbatim strings. No relations, explanation, extra fields, omitted sections or pagination.
FINAL OUTPUT CONTRACT: entity_instance and aliases MUST NOT contain any of these project-forbidden objects (case, typography and symbols do not create exceptions): ["Main-group patent share", "P_g", "Subgroup patent share", "p_i", "Human Development Index", "HDI", "Share of matched keywords", "P_link1", "P_link2", "P_semantic", "Acceptance time", "Number of pages", "Title length", "Average delay year", "Average intensity", "Average leading year", "Duration", "Frequency", "Coherence", "Topic coherence", "Perplexity", "theta(θ)", "%>0.50(%)", "Total docs >0.50", "hI annual", "hI norm", "papers per year", "coverage", "web links", "Average Overlap", "Time-Lagged Cross Correlation", "edge weight", "hyperprolific author ranking", "number of edges", "number of nodes"]. For the generic Frequency and Duration exclusions, compare the complete object rather than a substring: Frequency of research topics and explicitly defined journal/co-occurrence frequencies remain eligible. Do not globally block P_ symbols or all temporal measurements. Their yearly/full-network/backbone forms are forbidden too. They remain forbidden even if a preceding general coverage instruction appears to include them. Omit the objects rather than rename them, turn them into aliases or output empty properties for them. In contrast, keep EVERY source-supported h-index, g-index, P-index, x-index, full-count citations, Frequency of research topics, Citation count and Annual usage counts. Background named baselines are admissible even without current computation. If the introduction lists h-index, g-index and P-index as scientometric indicators, extract all three separately. If a new index is described as an adaptation of x-index, preserve the original x-index entity in addition to the new index. If cit is explicitly defined as full count citations, preserve that input as full-count citations, not just its normalized/fractional output. Do not treat a baseline mentioned with an author-year citation as a bibliography entry. The final array is an inventory of the admissible measures only, not your candidate working list.
FINAL NAME CHECK, AFTER all recall checks: entity_instance cannot equal "Frequency" or "Duration" (case-insensitive). In this project the standalone Frequency summary column, including frequency of terms appearing in patterns, is NOT an admitted Indicator. If an independently defined journal frequency or research-topic publication frequency exists, use its specific full source-bound name instead; do not rename the excluded column to bypass this rule. All explicitly excluded Human Development Index/HDI, Share of matched keywords, P_link1, P_link2, P_semantic, Acceptance time, Number of pages, Title length, Average delay year, Average intensity and Average leading year remain excluded even if other recall instructions mention their domain or their final outputs. No aliases or properties for an excluded object. Preserve source-supported Frequency of research topics and Annual usage counts.
One final project-specific boundary: the share/percentage/proportion of matched keywords or matched terms in company-involving papers is EXCLUDED, even if the authors use it for research-topic contribution. It is keyword matching, not an admissible publication share, citation share, or disciplinary diversity measure. Never return an Indicator named Share of matched keywords, Share of matched terms, Percentage of matched keywords, Percentage of matched terms or a renamed equivalent. Preserve genuinely defined shares of publications/citations; do not substitute their evidence for the excluded keyword-matching ratio. This final exclusion has priority over every general coverage example above.
""")
    inventory = request_stage(client, model, text, max_tokens, thinking,
                              "论文信息、指标清单与属性", PaperInventory, inventory_prompt)
    normalize_inventory(inventory, text)
    return inventory


def extract_relations(client, model: str, text: str, max_tokens: int, inventory: dict,
                      thinking: str = "enabled", *, relation_evidence: bool = False) -> dict:
    """One inventory-bound request for all eight labels, optionally with paragraph."""
    inventory = copy.deepcopy(inventory)
    paper_name = inventory["title"] or inventory["doi"]
    paper_entity = ({"entity_instance": paper_name, "entity_type": "Paper"} if paper_name else None)
    context = {"title": inventory["title"], "doi": inventory["doi"],
               "paper_entity": paper_entity, "entities_extracted": inventory["entities_extracted"],
               "indicator_details": inventory["indicator_details"]}
    names = {e["entity_instance"] for e in inventory["entities_extracted"]}
    triplets = []
    seen = set()
    schema = ParagraphRelationExtraction if relation_evidence else RelationExtraction
    evidence_instruction = (
        "\n本次同时输出关系与证据：每条关系增加paragraph字段，为原文中最相关的短连续片段。"
        "paragraph是一个字符串，逐字摘录，保留中间原文，不拼接、不改写、不增加其他证据字段。"
        "关系可依据全文多个位置判断；不能因一段不能容纳所有依据就漏掉有全文支持的候选。"
        if relation_evidence else
        "\n本次只输出关系，不输出paragraph或其他证据字段，关系证据留给后续检索。"
    )
    stage = request_stage(client, model, text, max_tokens, thinking,
        "关系与证据" if relation_evidence else "关系候选", schema,
        relation_task_prompt(RELATION_LABELS)
        + "\n一次检查全部八种标签。本次仅根据给定实体清单及原文识别关系，不增加实体。"
          "逐个相关实体核对每个标签，不能只执行第一个标签。"
          "指标端点必须使用清单规范名称；属性仅作身份定位，不能代替原文。"
        + evidence_instruction, context)
    partial = dict(entities_extracted=inventory["entities_extracted"], **stage)
    normalize_inventory(partial, text)
    for t in partial["kg_extraction_triplets"]:
        expected_source = "Paper" if t["relation"] in PAPER_RELATIONS else "Indicator"
        if t["source_entity_type"] != expected_source:
            raise ValueError("Relation task returned an incorrect source type")
        for side in ("source", "target"):
            if t[side + "_entity_type"] == "Indicator":
                field = side + "_entity_instance"
                if t[field] not in names:
                    # A second formatting pass may simplify nested math braces.
                    # Bind that spelling back to the exact supplied inventory name.
                    matches = [name for name in names
                               if canonical_spelling(name) == canonical_spelling(t[field])]
                    if len(matches) == 1:
                        t.setdefault('entity_name_normalization', {})[field] = t[field]
                        t[field] = matches[0]
                if t[field] not in names:
                    raise ValueError("Relation endpoint is outside the supplied inventory")
            else:
                field = side + "_entity_instance"
                if not paper_entity or canonical_spelling(t[field]) != canonical_spelling(paper_name):
                    raise ValueError("Relation Paper endpoint differs from the supplied paper")
                if t[field] != paper_name:
                    t.setdefault('entity_name_normalization', {})[field] = t[field]
                    t[field] = paper_name
        key = tuple(t[k] for k in ("source_entity_type", "source_entity_instance", "relation",
                                   "target_entity_type", "target_entity_instance"))
        if key not in seen:
            seen.add(key)
            triplets.append(t)
    result = Extraction.model_validate({**inventory, "kg_extraction_triplets": triplets}).model_dump()
    result["paper_entity"] = paper_entity
    aliases = {e["entity_instance"]: e.get("aliases", []) for e in result["entities_extracted"]}
    for t in result["kg_extraction_triplets"]:
        for side in ("source", "target"):
            if t[side + "_entity_type"] == "Indicator":
                t[side + "_entity_aliases"] = aliases[t[side + "_entity_instance"]]
    result["extraction_protocol"] = dict(
        inventory_passes=1, relation_label_groups=[list(RELATION_LABELS)],
        requests=2, relation_evidence="paragraph" if relation_evidence else "deferred",
        endpoint_constraint="inventory", gold_used=False)
    return result


def extract(client, model: str, text: str, max_tokens: int, thinking: str = "enabled",
            *, relation_evidence: bool = False) -> dict:
    """Two requests: shared joint inventory, then all inventory-bound relations."""
    inventory = extract_inventory(client, model, text, max_tokens, thinking)
    return extract_relations(client, model, text, max_tokens, inventory, thinking,
                             relation_evidence=relation_evidence)


def verify_evidence(data: dict, markdown: str) -> None:
    if not markdown.strip():
        raise ValueError("Markdown is empty; evidence cannot be verified.")
    for triplet in data["kg_extraction_triplets"]:
        triplet["evidence_match"] = match_units(triplet, markdown)
        triplet["evidence_validated"] = triplet["evidence_match"]["passed"]
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
    parser.add_argument("--model")
    parser.add_argument("--validator-model", help="Semantic validator model; defaults to the extraction model")
    parser.add_argument("--evaluator-model", help="Independent evaluator model; defaults to the extraction model")
    parser.add_argument("--base-url")
    parser.add_argument("--output-root", type=Path, default=SCRIPT_DIR / "output")
    parser.add_argument("--max-tokens", type=int, default=32768)
    parser.add_argument("--thinking", choices=("disabled", "enabled"), default="enabled",
                        help="Reasoning for source-level extraction; enabled improves recall audits")
    parser.add_argument("--relation-evidence", action=argparse.BooleanOptionalAction, default=True,
                        help="Use joint model evidence selection (default); --no-relation-evidence uses Top-k ablation")
    parser.add_argument("--evidence-rounds", type=int, default=1, choices=(0, 1))
    from compact_evidence import add_retrieval_arguments, create_index
    add_retrieval_arguments(parser,legacy_alias=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--extract-only", action="store_true", help="仅运行两次抽取请求，不进行本体、检索、验证或评估")
    add_backend_argument(parser)
    args = parser.parse_args()
    try:
        configure_backend(args)
    except ValueError as error:
        parser.error(str(error))
    if args.max_tokens <= 0:
        parser.error("max-tokens must be positive")
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
    if args.extract_only:
        paths = paths[:1]
    if not args.force and any(path.exists() for path in paths):
        parser.error("Output already exists; choose a new date or use --force to replace these stage outputs")
    try:
        markdown = args.markdown.expanduser().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        parser.error(f"Cannot read Markdown: {exc}")
    if not markdown.strip():
        parser.error("Markdown is empty")
    if not args.extract_only:
        try:
            evidence_index=create_index(markdown,args.evidence_retrieval,args.compact_bge)
        except (OSError,ImportError,ValueError) as exc:
            parser.error(f"Cannot initialize evidence retrieval: {exc}")
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        parser.error("Set DEEPSEEK_API_KEY in extraction/.env")
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        data = extract(client, args.model, markdown, args.max_tokens, args.thinking)
    write_json(paths[0], data)
    if args.extract_only:
        print(f"仅抽取完成（两次请求）：{paths[0]}", flush=True)
        return
    annotate_scientometrics(data)
    write_json(paths[1], data)
    validator_model = args.validator_model or args.model
    print(f"关系证据检索与验证：model={validator_model}", flush=True)
    with OpenAI(api_key=api_key, base_url=base_url, timeout=180, max_retries=2) as client:
        total, valid = collect_data(data, markdown, client, validator_model,
            method="coherent" if args.relation_evidence else "topk", max_rounds=args.evidence_rounds,
            index=evidence_index)
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
