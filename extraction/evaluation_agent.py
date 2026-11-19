"""
LLM evaluation pass for validated paper JSON files.

Default input:
  output/validated_papers/<date>/*.json

Default output:
  output/evaluated_papers/<date>/*.json
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
from functools import partial
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from prompts_zh import EVALUATION_PROMPT, multi_evidence_evaluation_prompt
from evidence_schema import evidence_text
from validation_agent import create_client, generate_label, resolve_model, parse_label
from tqdm import tqdm



SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_GPT_MODEL = "gpt-5-mini-2025-08-07"


def load_environment() -> None:
    load_dotenv()
    load_dotenv(SCRIPT_DIR / ".env")


def today() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def latest_validation_date() -> str:
    output_root = SCRIPT_DIR / "output" / "validated_papers"
    if not output_root.exists():
        return today()

    date_dirs = sorted(path.name for path in output_root.iterdir() if path.is_dir())
    if not date_dirs:
        return today()
    return date_dirs[-1]


def default_input_dir(date: str) -> Path:
    return SCRIPT_DIR / "output" / "validated_papers" / date


def default_output_dir(date: str) -> Path:
    return SCRIPT_DIR / "output" / "evaluated_papers" / date


def iter_json_files(input_dir: Path) -> Iterable[Path]:
    return sorted(path for path in input_dir.glob("*.json") if path.is_file())


def triplet_value(triplet: dict, key: str, fallback_key: str | None = None) -> str:
    value = triplet.get(key)
    if value is None and fallback_key is not None:
        value = triplet.get(fallback_key)
    return "" if value is None else str(value)


def evaluate_triplet(client, provider: str, model: str, triplet: dict) -> str:
    return evaluate_triplet_assessment(client,provider,model,triplet)['verdict']


def evaluate_triplet_assessment(client,provider: str,model: str,triplet: dict,*,requirements=None) -> dict:
    """Evidence-only independent assessment, without prior-stage flags or gold."""
    if provider=='deepseek':
        result=_evaluate_structured(client,model,triplet)
        return apply_evidence_requirements(result,triplet,requirements)
    template = multi_evidence_evaluation_prompt() if "evidence" in triplet or triplet.get("evidence_units") else EVALUATION_PROMPT
    prompt = template.format(
        source=triplet_value(
            triplet,
            "source_entity_instance_anaphora_resolved",
            "source_entity_instance",
        ),
        source_type=triplet_value(triplet, "source_entity_type"),
        target_type=triplet_value(triplet, "target_entity_type"),
        relation=triplet_value(triplet, "relation"),
        target=triplet_value(
            triplet,
            "target_entity_instance_anaphora_resolved",
            "target_entity_instance",
        ),
        paragraph=evidence_text(triplet),
    )

    label=generate_label(client, provider, model, prompt)
    return apply_evidence_requirements(dict(verdict='valid' if parse_label(label) else 'invalid',raw=label),triplet,requirements)


def apply_evidence_requirements(result,triplet,requirements=None):
    if requirements is None:return result
    from evaluation_rubric import check_requirements
    raw=triplet.get('evidence',triplet.get('evidence_units',triplet.get('paragraph','')))
    if isinstance(raw,str):raw=[raw]
    coverage=check_requirements([u if isinstance(u,str) else u['text'] for u in raw],requirements)
    result=dict(result,semantic_verdict=result['verdict'],reference_coverage=coverage)
    if not coverage['passed']:
        result['verdict']='invalid'
        result['missing_conditions']=result.get('missing_conditions',[])+['annotated_evidence_requirements']
    return result


def _evaluate_structured(client,model,triplet):
    from semantic_judgment import judge_triplet
    return judge_triplet(client,model,triplet,'evaluator')


def evaluate_data(data: dict, client, provider: str, model: str,*,requirements_by_key=None) -> tuple[int, int]:
    """Evaluate all triples independently; retain valid and all prior stage verdicts."""
    triplets = data.get("kg_extraction_triplets", [])
    if not isinstance(triplets, list):
        raise ValueError("kg_extraction_triplets must be a list")

    eval_valid_count = 0
    for triplet in triplets:
        if not isinstance(triplet, dict):
            raise ValueError("kg_extraction_triplets must contain objects")

        key=tuple(triplet_value(triplet,k) for k in ('source_entity_instance','relation','target_entity_instance'))
        requirement=(requirements_by_key or {}).get(key)
        details=evaluate_triplet_assessment(client,provider,model,triplet,requirements=requirement)
        if requirements_by_key is not None:
            details['reference_status']='configured' if key in requirements_by_key else 'not_configured'
        validation_result = details['verdict']
        is_valid = parse_label(validation_result)
        triplet["evaluation_response"] = validation_result
        triplet["eval"] = is_valid
        triplet['evaluation_details']=details
        if is_valid:
            eval_valid_count += 1

    return len(triplets), eval_valid_count


def process_file(
    json_path: Path,
    output_dir: Path,
    provider: str,
    model: str,
    requirements_by_key=None,
) -> tuple[int, int]:
    client = create_client(provider)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    triplet_count, eval_valid_count = evaluate_data(data, client, provider, model,requirements_by_key=requirements_by_key)

    output_path = output_dir / json_path.name
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")

    return triplet_count, eval_valid_count


def evaluate_papers(
    input_dir: Path,
    output_dir: Path,
    provider: str,
    model: str,
    max_workers: int,
    force: bool,
    requirements_by_key=None,
) -> dict[str, int]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Validated paper directory not found: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    all_json_paths = list(iter_json_files(input_dir))
    json_paths = [
        path
        for path in all_json_paths
        if force or not (output_dir / path.name).exists()
    ]

    stats = {
        "processed": 0,
        "skipped_existing": len(all_json_paths) - len(json_paths),
        "triplets": 0,
        "eval_valid_triplets": 0,
        "eval_invalid_triplets": 0,
        "failed": 0,
    }

    worker = partial(
        process_file,
        output_dir=output_dir,
        provider=provider,
        model=model,
        requirements_by_key=requirements_by_key,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, json_path): json_path for json_path in json_paths}
        for future in tqdm(
            concurrent.futures.as_completed(futures),
            total=len(futures),
            desc="Evaluating triplets",
        ):
            json_path = futures[future]
            try:
                triplet_count, eval_valid_count = future.result()
                stats["processed"] += 1
                stats["triplets"] += triplet_count
                stats["eval_valid_triplets"] += eval_valid_count
                stats["eval_invalid_triplets"] += triplet_count - eval_valid_count
            except Exception as exc:
                stats["failed"] += 1
                print(f"Failed: {json_path} - {exc}")

    return stats


def parse_args() -> argparse.Namespace:
    default_date = latest_validation_date()
    parser = argparse.ArgumentParser(
        description="Evaluate extracted triplets in validated paper JSON files."
    )
    parser.add_argument(
        "--date",
        default=default_date,
        help=(
            "Date partition to process. Defaults to the latest "
            "output/validated_papers/<date> directory."
        ),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Directory containing validated paper JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to output/evaluated_papers/<date>.",
    )
    parser.add_argument(
        "--model",
        default=(os.getenv("VLLM_MODEL", "Qwen/Qwen3-32B-AWQ")
                 if os.getenv("EXTRACTION_BACKEND") == "vllm"
                 else os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
        help=(
            "Model selector. Defaults to DEEPSEEK_MODEL or deepseek-chat. "
            "Pass 'gpt' to use gpt-5-mini-2025-08-07."
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=16,
        help="Number of concurrent files to process.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess files that already exist in the output directory.",
    )
    parser.add_argument('--evidence-rubric',type=Path,help='Optional JSON list with source/relation/target and OR-of-AND groups of required original quotes; local reject-only gate')
    return parser.parse_args()


def main() -> None:
    load_environment()
    args = parse_args()
    input_dir = args.input_dir or default_input_dir(args.date)
    output_dir = args.output_dir or default_output_dir(args.date)
    provider, resolved_model = resolve_model(args.model)
    requirements_by_key=None
    if args.evidence_rubric:
        entries=json.loads(args.evidence_rubric.read_text(encoding='utf-8-sig'))
        requirements_by_key={tuple(r[k] for k in ('source','relation','target')):r['groups'] for r in entries}

    stats = evaluate_papers(
        input_dir=input_dir,
        output_dir=output_dir,
        provider=provider,
        model=resolved_model,
        max_workers=args.max_workers,
        force=args.force,
        requirements_by_key=requirements_by_key,
    )

    print("\nEvaluation Summary")
    print(f"  Input dir: {input_dir}")
    print(f"  Output dir: {output_dir}")
    print(f"  Provider: {provider}")
    print(f"  Model: {resolved_model}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
