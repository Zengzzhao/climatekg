#!/usr/bin/env python3
"""Verify PDF/Markdown correspondence and local image references, without API calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


def verify(source, output, year):
    source_year, output_year = source / year, output / year
    if not source_year.is_dir():
        raise ValueError(f'输入年份目录不存在：{source_year}')
    state_path = output / '.mineru' / 'state.json'
    jobs = json.loads(state_path.read_text(encoding='utf-8'))['jobs'] if state_path.exists() else {}
    pdfs = sorted(p for p in source_year.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf')
    all_markdown = sorted(output_year.rglob('*.md'))
    mapped = set()
    documents = []
    for pdf in pdfs:
        relative = pdf.relative_to(source)
        folder = output / relative.with_suffix('')
        markdowns = sorted(folder.rglob('*.md')) if folder.is_dir() else []
        mapped.update(markdowns)
        issues, details = [], []
        if len(markdowns) != 1:
            issues.append(f'期望 1 个 Markdown，实际 {len(markdowns)} 个')
        job = jobs.get(str(relative), {})
        if job.get('state') != 'done':
            issues.append(f'状态未完成：{job.get("state", "missing")} {job.get("error", "")}')
        for md in markdowns:
            raw = md.read_bytes()
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                issues.append(f'Markdown 不是有效 UTF-8：{md.name}')
                text = ''
            if not text.strip():
                issues.append(f'Markdown 正文为空：{md.name}')
            references = re.findall(r'!\[[^\]]*\]\(([^)]+)\)', text)
            references += re.findall(r'<img\b[^>]*\bsrc=["\x27]([^"\x27]+)["\x27]', text, re.I)
            missing = []
            for reference in references:
                target = reference.strip()
                if target.startswith('<') and '>' in target:
                    target = target[1:target.index('>')]
                else:
                    target = re.split(r'\s+["\x27]', target, maxsplit=1)[0]
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                asset = md.parent / unquote(parsed.path)
                if not asset.is_file() or not asset.stat().st_size:
                    missing.append(target)
            if missing:
                issues.append(f'缺失或空图片：{missing}')
            details.append({'path': str(md.relative_to(output)), 'bytes': len(raw),
                            'sha256': hashlib.sha256(raw).hexdigest(),
                            'image_references': len(references), 'missing_images': missing})
        documents.append({'pdf': str(relative), 'state': job.get('state'),
                          'markdown': details, 'issues': issues})
    extras = [str(p.relative_to(output)) for p in all_markdown if p not in mapped]
    valid = bool(pdfs) and len(pdfs) == len(all_markdown) and not extras and not any(d['issues'] for d in documents)
    return {'year': year, 'source': str(source_year), 'output': str(output_year),
            'pdf_count': len(pdfs), 'markdown_count': len(all_markdown),
            'valid_documents': sum(not d['issues'] for d in documents),
            'state_counts': dict(Counter(d['state'] for d in documents)),
            'image_references': sum(m['image_references'] for d in documents for m in d['markdown']),
            'extra_markdown': extras, 'passed': valid, 'documents': documents}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('/Users/zengzizhao/Desktop/毕设论文/数据集'))
    parser.add_argument('--output', type=Path, default=Path('/Users/zengzizhao/Desktop/毕设论文/数据集md'))
    parser.add_argument('--year', required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = verify(args.input, args.output, args.year)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'documents'}, ensure_ascii=False, indent=2))
    for document in result['documents']:
        if document['issues']:
            print(document['pdf'], '; '.join(document['issues']))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
