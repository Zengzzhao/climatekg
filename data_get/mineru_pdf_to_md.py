#!/usr/bin/env python3
"""Convert a local PDF tree using MinerU's precise v4 API (default: vlm)."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sys
import tempfile
import time
import zipfile

import requests
from pypdf import PdfReader

DEFAULT_INPUT = Path('/Users/zengzizhao/Desktop/毕设论文/数据集')
DEFAULT_OUTPUT = Path('/Users/zengzizhao/Desktop/毕设论文/数据集md')
API = 'https://mineru.net/api/v4'


class ConversionError(Exception):
    pass


class AuthenticationError(ConversionError):
    pass


def log(message):
    print(message, flush=True)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)


class Client:
    def __init__(self, token, retries=4):
        self.token = token
        self.retries = retries

    def request(self, method, url, *, authenticated=False, file_path=None, **kwargs):
        # Authentication goes only to the MinerU API, never to signed OSS/CDN URLs.
        headers = {'Authorization': f'Bearer {self.token}'} if authenticated else {}
        for attempt in range(self.retries):
            try:
                with contextlib.ExitStack() as stack:
                    if file_path is not None:
                        kwargs['data'] = stack.enter_context(Path(file_path).open('rb'))
                    response = requests.request(method, url, headers=headers,
                                                timeout=(30, 300), **kwargs)
                if response.status_code in (401, 403) and authenticated:
                    response.close()
                    raise AuthenticationError('MinerU 拒绝 Token（HTTP 401/403），请检查精准解析 API Token。')
                if response.status_code == 429 or response.status_code >= 500:
                    status = response.status_code
                    response.close()
                    raise ConversionError(f'服务器暂时不可用：HTTP {status}')
                if not response.ok:
                    status = response.status_code
                    response.close()
                    raise ConversionError(f'HTTP {status}，请求失败（签名 URL 不写入日志）。')
                return response
            except (requests.RequestException, ConversionError) as exc:
                if isinstance(exc, AuthenticationError):
                    raise
                if attempt + 1 == self.retries:
                    # requests exceptions can contain credentials in signed URLs.
                    raise ConversionError(f'{method} 请求失败，已尝试 {self.retries} 次；请检查网络或服务状态。') from None
                time.sleep(min(2 ** (attempt + 1), 30))

    def api(self, method, endpoint, payload=None):
        with self.request(method, API + endpoint, authenticated=True, json=payload) as response:
            try:
                result = response.json()
            except ValueError:
                raise ConversionError('API 未返回有效 JSON。') from None
        code = result.get('code')
        if code in ('A0202', 'A0211'):
            raise AuthenticationError(f'MinerU Token 无效或已过期（{code}），请在 API 管理页面创建 Token。')
        if code != 0:
            raise ConversionError(f'MinerU API 错误 {code}: {result.get("msg", "未知错误")}')
        return result['data']


def save_result(client, url, destination):
    """Stage the ZIP; publish only after validating Markdown and archive paths."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mineru-', dir=destination.parent) as temporary:
        stage = Path(temporary)
        archive = stage / 'result.zip'
        with client.request('GET', url, stream=True) as response, archive.open('wb') as stream:
            for chunk in response.iter_content(1024 * 1024):
                stream.write(chunk)
        extracted = stage / 'extracted'
        extracted.mkdir()
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                if sum(m.file_size for m in members) > 4 * 1024**3:
                    raise ConversionError('结果解压大小超过 4 GiB。')
                for member in members:
                    part = PurePosixPath(member.filename)
                    if part.is_absolute() or '..' in part.parts or '\\' in member.filename:
                        raise ConversionError('结果 ZIP 含有不安全路径。')
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ConversionError('结果 ZIP 含符号链接。')
                # Retain original relative paths for all Markdown figures/assets.
                for member in members:
                    if not member.is_dir() and Path(member.filename).suffix.lower() not in ('.json', '.pdf'):
                        bundle.extract(member, extracted)
        except zipfile.BadZipFile:
            raise ConversionError('下载的结果不是有效 ZIP。') from None
        markdowns = sorted(extracted.rglob('*.md'))
        if not markdowns or not any(p.read_text(encoding='utf-8').strip() for p in markdowns):
            raise ConversionError('解析结果中没有非空 Markdown。')
        if destination.exists():
            raise ConversionError(f'输出目录已存在但未确认完成，请先移动该目录：{destination}')
        extracted.replace(destination)
        return [str(p.relative_to(extracted)) for p in markdowns]


def fingerprint(path, relative, args):
    stat = path.stat()
    value = [str(relative), stat.st_size, stat.st_mtime_ns, args.model, args.language, args.ocr]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def recover_legacy_preflight(job):
    """Only revive known local-reader failures, never remote parsing failures."""
    if (job['state'] == 'failed' and not job.get('batch_id')
            and job.get('error') == 'xref table read error'):
        warning = job.pop('error')
        job.update(state='new', preflight={'status': 'warning', 'warning': warning})
        return True
    return False


def retry_remote_failure(job, result, limit):
    """Retry only server failures explicitly identified as temporary, with a cap."""
    message = result.get('err_msg') or '解析失败'
    job.update(state='failed', error=message, failed_at=time.time())
    if 'err_code' in result:
        job['error_code'] = result['err_code']
    temporary = ('please try again later' in message.lower()
                 or '请稍后重试' in message or '服务暂时不可用' in message
                 or str(result.get('err_code')) in ('-60007', '-60009', '-60010', '-10001'))
    attempts = job.get('parse_retries', 0)
    if not temporary or attempts >= limit:
        return False
    history = {key: job[key] for key in ('batch_id', 'created', 'error', 'error_code', 'failed_at') if key in job}
    job.setdefault('failure_history', []).append(history)
    for key in ('batch_id', 'created', 'uploaded', 'upload_url', 'error', 'error_code', 'failed_at'):
        job.pop(key, None)
    job.update(state='new', parse_retries=attempts + 1)
    return True


def preflight(path, destination, job):
    """Cache page checks; inability to read locally must not block MinerU."""
    try:
        if destination.exists():
            raise ConversionError(f'输出已存在：{destination}；请移动后重试，以免覆盖。')
        if not 0 < path.stat().st_size <= 200 * 1024**2:
            raise ConversionError('文件为空或大于 200 MB。')
    except (ConversionError, OSError) as exc:
        job.update(state='failed', error=str(exc))
        return
    if job.get('preflight'):
        return
    # Older states may already contain a successful page count.
    pages = job.get('pages')
    if pages is None:
        # Silence pypdf's verbose repair diagnostics only during this read.
        logger = logging.getLogger('pypdf')
        previous_handlers, previous_propagate = logger.handlers, logger.propagate
        logger.handlers, logger.propagate = [logging.NullHandler()], False
        try:
            with path.open('rb') as stream:
                pages = len(PdfReader(stream, strict=False).pages)
        except Exception as exc:
            job['preflight'] = {'status': 'warning', 'warning': f'{type(exc).__name__}: {exc}'}
            return
        finally:
            logger.handlers, logger.propagate = previous_handlers, previous_propagate
    job['pages'] = pages
    if not 0 < pages <= 200:
        job.update(state='failed', error=f'共 {pages} 页，超出 API 的 1–200 页范围；请拆分后重试。')
        return
    job['preflight'] = {'status': 'ok'}


def run(args):
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_dir():
        raise ConversionError(f'输入目录不存在：{source}')
    files = sorted((p for p in source.rglob('*') if p.is_file() and p.suffix.lower() == '.pdf'),
                   key=lambda p: str(p.relative_to(source)))
    if args.year:
        files = [p for p in files if p.relative_to(source).parts[0] in args.year]
    if args.limit:
        files = files[:args.limit]
    log(f'选中 {len(files)} 个 PDF；精准 API / {args.model}；输出：{output}')
    if not files:
        return 0
    # A directory per PDF prevents figure name collisions between papers.
    targets = [output / p.relative_to(source).with_suffix('') for p in files]
    if len({str(p).casefold() for p in targets}) != len(targets):
        raise ConversionError('输入存在同名 PDF（忽略扩展名/大小写），输出目录会冲突，请先重命名。')
    if args.dry_run:
        for path, destination in zip(files, targets):
            log(f'{path.relative_to(source)} -> {destination}/full.md')
        return 0
    token_path = args.token_file.expanduser()
    token = os.environ.get('MINERU_TOKEN') or os.environ.get('MINERU_API_TOKEN')
    if not token and token_path.is_file():
        token = token_path.read_text(encoding='utf-8').strip()
    if not token:
        raise AuthenticationError(f'缺少 Token：设置 MINERU_TOKEN，或将 Token 保存到 {token_path}')
    token = token.strip().removeprefix('Bearer ').strip()
    client = Client(token)
    state_dir = output / '.mineru'
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ConversionError('同一个输出目录已有脚本运行。') from None
        return convert(args, source, output, files, targets, client, state_dir / 'state.json')


def convert(args, source, output, files, targets, client, state_path):
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'version': 1, 'jobs': {}}
    jobs = state['jobs']
    selected = []
    def save():
        atomic_json(state_path, state)

    for path, destination in zip(files, targets):
        key = str(path.relative_to(source))
        signature = fingerprint(path, key, args)
        job = jobs.get(key)
        if job and job['signature'] != signature:
            raise ConversionError(f'源文件或解析参数已变化：{key}。请使用新的 --output 目录，避免混合结果。')
        if not job:
            job = jobs[key] = {'signature': signature, 'state': 'new', 'output': str(destination.relative_to(output))}
        if recover_legacy_preflight(job):
            log(f'恢复旧版预检查误跳过文件，将交给 MinerU：{key}')
        if job['state'] == 'done':
            if not all((destination / name).is_file() and (destination / name).stat().st_size for name in job.get('markdown', ['full.md'])):
                raise ConversionError(f'已完成的输出被删除或清空：{destination}，请恢复文件或使用新输出目录。')
            log(f'跳过已完成：{key}')
        elif job['state'] == 'failed' and args.retry_failed:
            job.clear()
            job.update(signature=signature, state='new', output=str(destination.relative_to(output)))
        selected.append((key, path, destination, job))
    save()
    # Process one batch at a time to bound API queue load and retain restart state.
    pending = [item for item in selected if item[3]['state'] not in ('done', 'failed')]
    while pending:
        first = pending[0][3]
        if first.get('batch_id'):
            batch = [item for item in pending if item[3].get('batch_id') == first['batch_id']]
        else:
            batch = [item for item in pending if not item[3].get('batch_id')][:args.batch_size]
            log(f'准备下一批 {len(batch)} 个文件（逐个保存预检查断点）')
            for key, path, dest, job in batch:
                preflight(path, dest, job)
                save()
                if job['state'] == 'failed':
                    log(f'预检查失败：{key}: {job["error"]}')
                elif job['preflight']['status'] == 'warning':
                    log(f'页数读取警告，将交给 MinerU 完整解析：{key}: {job["preflight"]["warning"]}')
            rejected = [item for item in batch if item[3]['state'] == 'failed']
            pending = [item for item in pending if item not in rejected]
            batch = [item for item in batch if item[3]['state'] != 'failed']
            if not batch:
                continue
            payload = {'model_version': args.model, 'language': args.language,
                       'enable_formula': True, 'enable_table': True,
                       'files': [{'name': path.name, 'data_id': job['signature'], 'is_ocr': args.ocr}
                                 for key, path, dest, job in batch]}
            data = client.api('POST', '/file-urls/batch', payload)
            urls = data['file_urls']
            if len(urls) != len(batch):
                raise ConversionError('API 上传链接数量与文件数量不一致。')
            for item, url in zip(batch, urls):
                item[3].update(batch_id=data['batch_id'], upload_url=url, state='waiting-file', created=time.time())
            save()
            log(f'创建批次 {data["batch_id"]}，{len(batch)} 个文件')
        for key, path, dest, job in batch:
            if job.get('uploaded'):
                continue
            if time.time() - job['created'] >= 23 * 3600:
                job.update(state='failed', error='上传链接已过期，使用 --retry-failed 重新申请。')
                save()
                continue
            try:
                with client.request('PUT', job['upload_url'], file_path=path):
                    pass
                job.update(uploaded=True, state='pending')
                job.pop('upload_url', None)
                save()
                log(f'上传完成：{key}（{job.get("pages", "?")} 页）')
            except ConversionError as exc:
                # Retain signed upload URL and batch ID so a rerun retries upload.
                log(f'上传失败，保留断点：{key}: {exc}')
        active = [item for item in batch if item[3].get('uploaded') and item[3]['state'] != 'failed']
        batch_id = batch[0][3]['batch_id']
        deadline = time.monotonic() + args.max_wait
        while active:
            data = client.api('GET', '/extract-results/batch/' + batch_id)
            results = data.get('extract_result', [])
            for item in list(active):
                key, path, dest, job = item
                result = next((r for r in results if r.get('data_id') == job['signature']), None)
                # Older responses may omit data_id; only use unambiguous names.
                if result is None:
                    matches = [r for r in results if r.get('file_name') == path.name]
                    if len(matches) == 1 and sum(x[1].name == path.name for x in batch) == 1:
                        result = matches[0]
                if result is None:
                    continue
                status = result['state']
                if job['state'] != status:
                    log(f'{key}: {status}')
                if status == 'done':
                    try:
                        job['markdown'] = save_result(client, result['full_zip_url'], dest)
                        job.update(state='done', completed=time.time())
                        log(f'保存成功：{dest}（Markdown + 配图）')
                    except (ConversionError, OSError, requests.RequestException) as exc:
                        log(f'下载/保存失败，保留远程任务供下次重试：{key} ({type(exc).__name__})')
                    active.remove(item)
                elif status == 'failed':
                    limit = getattr(args, 'parse_retries', 2)
                    if retry_remote_failure(job, result, limit):
                        log(f'服务端临时解析失败，安排自动重试 {job["parse_retries"]}/{limit}：{key}')
                    else:
                        log(f'解析失败：{key}: {job["error"]}')
                    active.remove(item)
                else:
                    job['state'] = status
                save()
            if active:
                if time.monotonic() >= deadline:
                    if getattr(args, 'watch', False):
                        log('本批仍在等待，已保存断点；轮询其他批次后继续查询。')
                    else:
                        log('本批等待超时，已保存断点；下次运行继续查询。')
                    break
                time.sleep(args.poll_interval)
        pending = [item for item in pending if item not in batch]
        pending.extend(item for item in batch if item[3]['state'] == 'new')
        if getattr(args, 'watch', False):
            # Rotate unfinished remote batches so one long queue does not block
            # submissions or downloads belonging to other batches.
            retry = [item for item in batch if item[3].get('uploaded')
                     and item[3]['state'] not in ('done', 'failed')]
            if retry:
                pending.extend(retry)
                time.sleep(args.poll_interval)
    done = sum(item[3]['state'] == 'done' for item in selected)
    failed = sum(item[3]['state'] == 'failed' for item in selected)
    log(f'本次选中 {len(selected)}：成功 {done}，失败 {failed}，待继续 {len(selected)-done-failed}。')
    log(f'状态记录：{state_path}')
    return 0 if done == len(selected) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--year', action='append', help='仅转换指定年份，可重复，如 --year 2024 --year 2023')
    parser.add_argument('--limit', type=int, help='仅选排序后的前 N 个 PDF；已完成文件也计入 N')
    parser.add_argument('--batch-size', type=int, default=10, help='每批文件数，1–50（默认 10）')
    parser.add_argument('--model', choices=('vlm', 'pipeline'), default='vlm')
    parser.add_argument('--language', default='en', help='默认 en（英文论文）；中文用 ch')
    parser.add_argument('--ocr', action='store_true', help='强制 OCR，主要用于扫描 PDF')
    parser.add_argument('--token-file', type=Path, default=Path(__file__).resolve().with_name('.mineru-token'))
    parser.add_argument('--poll-interval', type=float, default=10)
    parser.add_argument('--max-wait', type=float, default=7200, help='每批最大等待秒数，超时可续跑')
    parser.add_argument('--watch', action='store_true', help='轮流查询未完成批次，直到远程任务全部结束；可配合 --max-wait 20 使用')
    parser.add_argument('--retry-failed', action='store_true', help='重新提交上次失败的文件（成功文件仍跳过）')
    parser.add_argument('--parse-retries', type=int, default=2, help='服务端临时解析失败的自动重试次数（默认 2，0 禁用）')
    parser.add_argument('--dry-run', action='store_true', help='仅列出选中文件，不调用 API、不写文件')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50 or (args.limit is not None and args.limit <= 0) or args.poll_interval <= 0 or args.max_wait <= 0 or args.parse_retries < 0:
        parser.error('batch-size 需为 1–50，limit / poll-interval / max-wait 必须为正数，parse-retries 必须非负。')
    try:
        return run(args)
    except KeyboardInterrupt:
        log('已中断，重新运行相同命令可继续。')
        return 130
    except (ConversionError, OSError, ValueError) as exc:
        log(f'错误：{exc}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
