"""
LLM validation pass for evidence-validated paper JSON files.

Default input:
  output/evidence_validated_papers/<date>/*.json

Default output:
  output/validated_papers/<date>/*.json
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import json
import os
import re
from functools import partial
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from prompts_zh import VALIDATION_PROMPT, MULTI_EVIDENCE_READING
from evidence_schema import evidence_text
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


def latest_evidence_validation_date() -> str:
    output_root = SCRIPT_DIR / "output" / "evidence_validated_papers"
    if not output_root.exists():
        return today()

    date_dirs = sorted(path.name for path in output_root.iterdir() if path.is_dir())
    if not date_dirs:
        return today()
    return date_dirs[-1]


def default_input_dir(date: str) -> Path:
    return SCRIPT_DIR / "output" / "evidence_validated_papers" / date


def default_output_dir(date: str) -> Path:
    return SCRIPT_DIR / "output" / "validated_papers" / date


def iter_json_files(input_dir: Path) -> Iterable[Path]:
    return sorted(path for path in input_dir.glob("*.json") if path.is_file())


def triplet_value(triplet: dict, key: str, fallback_key: str | None = None) -> str:
    value = triplet.get(key)
    if value is None and fallback_key is not None:
        value = triplet.get(fallback_key)
    return "" if value is None else str(value)


def resolve_model(model_arg: str) -> tuple[str, str]:
    if model_arg.lower() == "gpt":
        return "gpt", DEFAULT_GPT_MODEL
    if model_arg.lower().startswith(("deepseek", "cuc/")):
        return "deepseek", model_arg
    return "gemini", model_arg


def create_client(provider: str):
    if provider in ("gpt", "deepseek"):
        from openai import OpenAI
        if provider == "deepseek":
            return OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                          base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                          timeout=180, max_retries=2)
        return OpenAI()
    from google import genai

    return genai.Client()


def generate_label(client, provider: str, model: str, prompt: str) -> str:
    if provider == "gpt":
        response = client.responses.create(
            model=model,
            input=prompt,
        )
        return response.output_text.strip()

    if provider == "deepseek":
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=128,
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise ValueError(f"Validator incomplete: finish_reason={choice.finish_reason}")
        return (choice.message.content or "").strip()

    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    return (response.text or "").strip()


def parse_label(response: str) -> bool:
    """Accept a binary label, never silently count malformed API output as false."""
    label = response.strip().strip('`\"\'').strip().lower()
    if not re.fullmatch(r"valid|invalid", label):
        raise ValueError(f"Expected valid/invalid, got {response!r}")
    return label == "valid"


def validate_triplet(client, provider: str, model: str, triplet: dict) -> str:
    return generate_label(client, provider, model, validation_prompt(triplet))


def validation_prompt(triplet: dict) -> str:
    template = VALIDATION_PROMPT
    if "evidence" in triplet or triplet.get("evidence_units"):
        template += MULTI_EVIDENCE_READING
    prompt = template.format(
        source=triplet_value(
            triplet,
            "source_entity_instance_anaphora_resolved",
            "source_entity_instance",
        ),
        source_type=triplet_value(triplet, "source_entity_type"),
        relation=triplet_value(triplet, "relation"),
        target=triplet_value(
            triplet,
            "target_entity_instance_anaphora_resolved",
            "target_entity_instance",
        ),
        target_type=triplet_value(triplet, "target_entity_type"),
        paragraph=evidence_text(triplet),
    )

    return prompt


def parse_feedback(content: str) -> dict:
    """Malformed output is an execution error, never a semantic rejection."""
    result = json.loads(content)
    if not isinstance(result, dict) or result.get('verdict') not in ('valid', 'invalid'):
        raise ValueError('Validator feedback must contain a valid/invalid verdict')
    if not isinstance(result.get('reason'), str) or not result['reason'].strip():
        raise ValueError('Validator feedback must contain a reason')
    for field in ('missing_evidence', 'search_queries'):
        values = result.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f'Validator feedback must contain a string array: {field}')
    if result['verdict'] == 'invalid' and not result['missing_evidence']:
        raise ValueError('Rejected evidence requires a concrete evidence requirement')
    if result['verdict'] == 'invalid' and not result['search_queries']:
        raise ValueError('Rejected evidence requires targeted retrieval queries')
    if result['verdict'] == 'valid' and (result['missing_evidence'] or result['search_queries']):
        raise ValueError('Valid evidence must not report missing content')
    return result


def validate_triplet_with_feedback(client, model: str, triplet: dict) -> dict:
    """New pipeline: one verdict plus actionable evidence needs in one call."""
    prompt = validation_prompt(triplet).replace('只回答 valid 或 invalid，不解释。', '')
    instruction = '''只返回 JSON：{"verdict":"invalid","reason":"具体判定理由","missing_evidence":["待核查的原文内容"],"search_queries":["定向检索词"]}。
invalid 时说明缺口或反证，用准确指标名写出需核查的内容，并给出原文语言的检索词。
不假造预期引文。若有矛盾，补检须核查反证或消歧，不预设关系成立。
valid 时两个数组均为空。诊断只用于补检。'''
    response = client.chat.completions.create(model=model, temperature=0, max_tokens=2000,
        response_format={'type':'json_object'}, extra_body={'thinking':{'type':'disabled'}},
        messages=[{'role':'system','content':instruction}, {'role':'user','content':prompt}])
    choice = response.choices[0]
    if choice.finish_reason != 'stop':
        raise ValueError(f'Validator feedback incomplete: {choice.finish_reason}')
    result = parse_feedback(choice.message.content or '')
    result['model'] = response.model
    result['response_id'] = response.id
    result['usage'] = response.usage.model_dump() if response.usage else None
    return result


_CONDITION_CHECKS = {
    'improves_on': {
        'limitation': ('原指标在给定任务下的具体局限或能力缺口。', 'target'),
        'mechanism': ('某个设计如何应对该同一局限或提供对应评价能力。', None),
        'source_design': ('具名来源采用该设计，且确实对齐同一局限和任务。', 'source'),
    },
    'alternative_to': {
        'eligibility': ('具名来源被直接允许替代，或属于替代规则所覆盖的变体组。', 'source'),
        'rule': ('对具名原指标明确规定同一任务的替代规则及原文条件。', 'target'),
        'scope': ('替代规则、具名变体资格和原指标版本指向同一任务及同一组。', None),
    },
    'component_of': {
        'input_identity': ('从原文定位来源定量测度及其值。正式定义的统计特征、同义描述或符号可以绑定来源。', None),
        'dependency': ('给出来源定量结果进入目标的实际计算链。直接计算或经同一中间量逐跳传递均可，包括按该特征形成排序再聚合该排序。每一跳的版本与方向必须有原文依据。', None),
        'output_identity': ('从原文定位实际接收最后一步输入的目标测度或聚合排名。', None),
    },
}


def validate_relation_conditions(client, model: str, triplet: dict) -> dict:
    """Experimental Validator with per-condition witnesses and endpoint checks."""
    from evidence_index import compact_endpoint_match

    relation = triplet['relation']
    if relation not in _CONDITION_CHECKS:
        return validate_triplet_with_feedback(client, model, triplet)
    checks = _CONDITION_CHECKS[relation]
    units = {u['id']:u['text'] for u in triplet['evidence']}
    prompt = validation_prompt(triplet).replace('只回答 valid 或 invalid，不解释。', '')
    instruction = '''分别判断每个条件。仅引用所给原文的 ID。每项都要回答 supported、missing 或 contradicted。
若条件需要跨片段绑定具名端点，ids 同时列出条件本身与明确识别该端点版本的原文片段。名称引句与局限引句可以分开，但二者都要有原文依据。
条件替代时，若候选两端按相反顺序排列，则先按证据确定原指标和可替代的具名变体，再核对条件。
将样本状态单独写入 empirical_scope。设计针对潜在局限，与某样本的实测改善是不同命题。
样本未观察到局限或数值改善，与确有对应设计兼容。样本未满足替代条件，与条件替代规则兼容。
如果证据明确否定设计与局限的对应，或者规则明确排除具名变体，才按该否定判相关条件。
不得把其他版本的证据归给候选。不得使用仅来自候选字段的名字作为原文证据。
先从原文分别识别实际的作用方和受作用方，再独立核对是否与指定候选一致。endpoint_alignment 的值用 bound、missing 或 mismatched。
component_of 的 source 必须是输入，target 必须是输出。若理由中的计算链与候选方向相反，标 mismatched，即使反方向成立。
component_of 还要在 endpoint_alignment 中用 observed_input、observed_output 写出原文实际作用方向两端的名称，只填名称或空字符串。依据原文识别，不能按候选端点顺序抄写。
improves_on 的 source 必须是实际采用应对机制的版本。另一版本采用机制或同一摘要只列出候选名称均不足。若理由只证明另一个版本，标 missing 或 mismatched。
alternative_to 为对称关系，按证明方向核对原指标与替代指标身份，无需对应候选端点顺序。
只返回 JSON，例如 {"checks":{"条件标识":{"status":"supported","ids":["给定原文ID"],"reason":"依据"}},"endpoint_alignment":{"source":"bound","target":"bound","reason":"按关系方向明确写出实际端点和候选的对应"},"empirical_scope":"观察结果或未提供"}。
'''
    prompt += '\nchecks 必须恰好包含下列全部键。不要用候选名称或三元组作为键。\n'
    prompt += json.dumps({'required_checks':{k:v[0] for k,v in checks.items()}},ensure_ascii=False)
    schema = dict(
        checks={k:dict(status='missing',ids=[],reason='原文依据') for k in checks},
        endpoint_alignment=dict(source='missing',target='missing',reason='候选与原文端点的对应'),
        empirical_scope='未提供',
    )
    if relation=='component_of':
        schema['endpoint_alignment'].update(observed_input='',observed_output='')
    prompt += '\n严格使用下列 JSON 的全部键，按证据替换示例值。\n'
    prompt += json.dumps(schema,ensure_ascii=False)
    response = client.chat.completions.create(model=model,temperature=0,max_tokens=2400,
        response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}},
        messages=[{'role':'system','content':instruction},{'role':'user','content':prompt}])
    choice = response.choices[0]
    if choice.finish_reason != 'stop':
        raise ValueError(f'Condition Validator incomplete: {choice.finish_reason}')
    result = json.loads(choice.message.content or '')
    rows = result['checks']
    empirical = result['empirical_scope']
    alignment = result['endpoint_alignment']
    if not isinstance(rows, dict) or set(rows)!=set(checks) or not isinstance(empirical,str):
        raise ValueError('Condition Validator returned unexpected checks')
    if (not isinstance(alignment,dict) or not isinstance(alignment.get('reason'),str)
            or any(alignment.get(side) not in {'bound','missing','mismatched'} for side in ('source','target'))):
        raise ValueError('Condition Validator returned malformed endpoint alignment')
    if relation=='component_of':
        actual = [alignment.get('observed_input'),alignment.get('observed_output')]
        if any(not isinstance(name,str) for name in actual):
            raise ValueError('Condition Validator omitted observed dependency endpoints')
        for i,side in enumerate(('source','target')):
            name = triplet[f'{side}_entity_instance']
            if compact_endpoint_match(name,actual[1-i]) and not compact_endpoint_match(name,actual[i]):
                alignment[side] = 'mismatched'
                alignment['reason'] = f'原文计算方向为 {actual[0]} → {actual[1]}，与候选方向不符。'
    for key, row in rows.items():
        ids = row.get('ids')
        if (row.get('status') not in {'supported','missing','contradicted'}
                or not isinstance(ids,list) or any(not isinstance(i,str) or i not in units for i in ids)
                or not isinstance(row.get('reason'),str) or not row['reason'].strip()):
            raise ValueError(f'Malformed condition check {key}')
        if row['status']=='supported' and not ids:
            row.update(status='missing', anchor_error='没有提供支持此条件的原文 ID')

    def anchored(swapped=False):
        failures = []
        witnesses = {}
        for key, (_,side) in checks.items():
            row = rows[key]
            if not side or row['status']!='supported':
                continue
            if swapped:
                side = 'source' if side=='target' else 'target'
            name = triplet[f'{side}_entity_instance']
            # A limitation may use anaphora after a named base definition.
            # Its semantic scope was judged using the entire evidence array.
            anchor_scope = units if key=='limitation' else row['ids']
            anchor_ids = [i for i in anchor_scope if compact_endpoint_match(name,units[i])]
            if anchor_ids:
                witnesses[key] = anchor_ids
            else:
                failures.append((key,name))
        return failures,witnesses

    failures,witnesses = anchored()
    if relation=='alternative_to' and failures:
        failures,witnesses = min(((failures,witnesses),anchored(swapped=True)),key=lambda v:len(v[0]))
    for key,ids in witnesses.items():
        rows[key]['anchor_ids'] = ids
    for key,name in failures:
        rows[key].update(status='missing',anchor_error=f'引用片段中未绑定具名端点 {name}')
    gaps = [f"{checks[k][0]} {r.get('anchor_error',r['reason'])}"
            for k,r in rows.items() if r['status']!='supported']
    reason = '；'.join(f"{k}={r['status']} {r.get('anchor_error',r['reason'])}" for k,r in rows.items())
    queries = [
        f"{triplet['source_entity_instance']} {triplet['target_entity_instance']} {relation} {k}"
        for k,r in rows.items() if r['status']!='supported'
    ]
    if any(alignment[side]!='bound' for side in ('source','target')):
        gaps.append(alignment['reason'] or '候选端点缺失或方向不符')
        reason += '；端点核对：'+gaps[-1]
        queries.append(f"{triplet['source_entity_instance']} {triplet['target_entity_instance']} {relation} endpoint identity")
    return dict(verdict='invalid' if gaps else 'valid',reason=reason,
                missing_evidence=gaps,search_queries=queries,checks=rows,
                endpoint_alignment=alignment,empirical_scope=empirical,
                model=response.model,response_id=response.id,
                usage=response.usage.model_dump() if response.usage else None)


def validate_data(data: dict, client, provider: str, model: str) -> tuple[int, int]:
    """Validate every triple, retaining prior verdicts as in the original climate pipeline."""
    triplets = data.get("kg_extraction_triplets", [])
    if not isinstance(triplets, list):
        raise ValueError("kg_extraction_triplets must be a list")
    valid_count = 0
    for triplet in triplets:
        if not isinstance(triplet, dict):
            raise ValueError("kg_extraction_triplets must contain objects")

        validation_result = validate_triplet(client, provider, model, triplet)
        is_valid = parse_label(validation_result)
        triplet["validation_response"] = validation_result
        triplet["valid"] = is_valid
        if is_valid:
            valid_count += 1

    return len(triplets), valid_count


def process_file(
    json_path: Path,
    output_dir: Path,
    provider: str,
    model: str,
) -> tuple[int, int]:
    client = create_client(provider)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    triplets = data.get("kg_extraction_triplets", [])
    if not isinstance(triplets, list):
        raise ValueError("kg_extraction_triplets must be a list")

    triplet_count, valid_count = validate_data(data, client, provider, model)

    output_path = output_dir / json_path.name
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
        f.write("\n")

    return triplet_count, valid_count


def validate_papers(
    input_dir: Path,
    output_dir: Path,
    provider: str,
    model: str,
    max_workers: int,
    force: bool,
) -> dict[str, int]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Evidence-validated paper directory not found: {input_dir}")

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
        "valid_triplets": 0,
        "invalid_triplets": 0,
        "failed": 0,
    }

    worker = partial(
        process_file,
        output_dir=output_dir,
        provider=provider,
        model=model,
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, json_path): json_path for json_path in json_paths}
        for future in tqdm(
            concurrent.futures.as_completed(futures),
            total=len(futures),
            desc="Validating triplets",
        ):
            json_path = futures[future]
            try:
                triplet_count, valid_count = future.result()
                stats["processed"] += 1
                stats["triplets"] += triplet_count
                stats["valid_triplets"] += valid_count
                stats["invalid_triplets"] += triplet_count - valid_count
            except Exception as exc:
                stats["failed"] += 1
                print(f"Failed: {json_path} - {exc}")

    return stats


def parse_args() -> argparse.Namespace:
    default_date = latest_evidence_validation_date()
    parser = argparse.ArgumentParser(
        description="Validate extracted triplets in evidence-validated paper JSON files."
    )
    parser.add_argument(
        "--date",
        default=default_date,
        help=(
            "Date partition to process. Defaults to the latest "
            "output/evidence_validated_papers/<date> directory."
        ),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        help="Directory containing evidence-validated paper JSON files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory. Defaults to output/validated_papers/<date>.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
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
    return parser.parse_args()


def main() -> None:
    load_environment()
    args = parse_args()
    input_dir = args.input_dir or default_input_dir(args.date)
    output_dir = args.output_dir or default_output_dir(args.date)
    provider, resolved_model = resolve_model(args.model)

    stats = validate_papers(
        input_dir=input_dir,
        output_dir=output_dir,
        provider=provider,
        model=resolved_model,
        max_workers=args.max_workers,
        force=args.force,
    )

    print("\nValidation Summary")
    print(f"  Input dir: {input_dir}")
    print(f"  Output dir: {output_dir}")
    print(f"  Provider: {provider}")
    print(f"  Model: {resolved_model}")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
