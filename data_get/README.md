# MinerU 精准解析：PDF → Markdown

依据 [MinerU API 文档](https://mineru.net/apiManage/docs)，使用精准解析 v4 API：
申请本地文件上传链接 → PUT 上传 → 轮询批次 → 下载 ZIP 并保存 Markdown / 配图。
默认 `model_version=vlm`，开启表格、公式识别，全文处理，不设置页码截断。
默认语言 `en`（当前数据为英文论文）；中文论文可加 `--language ch`。

## 执行全部数据

在终端运行（默认输入、输出已经设置为你的目录）：

```bash
cd /Users/zengzizhao/Desktop/climatekg
bash data_get/run_mineru.sh
```

只试跑 2024 年按文件名排序的前 3 个 PDF：

```bash
bash data_get/run_mineru.sh --year 2024 --limit 3
```

仅查看选中文件，不联网、不写文件：

```bash
bash data_get/run_mineru.sh --year 2024 --limit 3 --dry-run
```

输入默认 `/Users/zengzizhao/Desktop/毕设论文/数据集`，递归查找所有 PDF（扩展名不区分大小写）。
输出默认 `/Users/zengzizhao/Desktop/毕设论文/数据集md`，结构如下：

```text
数据集md/
  2024/
    论文名称/
      full.md
      images/...
  2023/...
  .mineru/
    state.json
    run.lock
```

每篇单独建目录，保留服务返回的 Markdown 文件名和图片相对路径，避免论文间配图覆盖。
默认不保留 ZIP、JSON、PDF 副本；其他配套资源保留。原始 PDF 不修改。

## Token 和依赖

脚本依次读取 `MINERU_TOKEN`、`MINERU_API_TOKEN`、脚本旁的 `.mineru-token` 文件。
`.mineru-token` 只放 Token 一行，已加入本目录的 `.gitignore`，不会进入 Git。
不要使用 Access Key / Secret Key 替代精准 API 的 Bearer Token。

当前项目虚拟环境已有依赖。新环境可安装：

```bash
python3 -m pip install -r data_get/requirements.txt
python3 data_get/mineru_pdf_to_md.py --year 2024 --limit 3
```

`run_mineru.sh` 优先使用项目 `.venv/bin/python`，可通过 `MINERU_PYTHON` 指定 Python。
启动器也处理本机 Homebrew Python 3.10 的 libexpat 加载兼容问题，仅影响当前运行进程。

## 中断、重试和限制

- 每批默认 10 个文件，可用 `--batch-size 1` 至 `50` 调整。
- `Ctrl+C` 后重新执行相同命令：已完成文件跳过，已上传任务继续查询，上传失败文件重新上传。
- 每次只预检查下一批文件，每个检查结果立即保存；中断后复用已保存的结果，不会重新读取所有 PDF。
- 不带 `--year` 和 `--limit` 即转换全部年份；先前成功的 3 个试跑文件会跳过。
- 网络请求最多尝试 4 次；批次默认等待至多 7200 秒，超时保留断点，下次继续。
- `--watch --batch-size 50 --max-wait 20` 会轮流查询未完成批次，先让后续文件也进入队列，再持续下载完成结果，直到远程任务结束。
- `--retry-failed` 重新提交失败文件；成功的文件仍跳过。可先用 `--year` 缩小范围。
- 服务端明确提示“稍后重试”的临时解析失败，默认自动重新提交最多 2 次；`--parse-retries 0` 可关闭。自动重试次数和失败批次保存在状态中，中断后继续累计。其他解析失败仍需排查或显式重试。
- 文件超过 200 MB 或 200 页会明确记录为失败，不会截断论文；请先自行拆分。
- 本地 PDF 库无法读取页数时，只记录一条警告，继续交给 MinerU 全文解析；此时由服务端检查页数限制。PDF 库的内部修复日志已收敛。
- 旧版因 `xref table read error` 在上传前跳过的文件，下次运行会自动恢复，无须加 `--retry-failed`；服务端解析失败的文件仍需显式重试。
- 如果源文件或模型/语言/OCR 参数发生变化，请指定新的 `--output`，避免混合不同结果。
- 输出目录已有文件但没有完成记录时，脚本拒绝覆盖，请先将对应目录移开再继续。
- 同一个输出目录只允许一个进程运行，避免状态文件冲突。

状态文件保存每个文件的远程批次、进度、失败原因和输出位置，不保存 Token。
尚未上传时会暂存有效期内的签名上传 URL，状态文件权限设为 `600`。
退出码：全部选中文件成功为 `0`；失败或仍待继续为 `1`；手动中断为 `130`。

更多参数：`bash data_get/run_mineru.sh --help`。

## 转换后核验

检查某年份 PDF 与 Markdown 是否逐篇对应、数量一致，正文是否为非空 UTF-8，以及 Markdown/HTML 图片引用是否存在：

```bash
python3 data_get/verify_mineru_output.py --year 2024 --report data_get/mineru_2024_verification.json
```

报告包含逐篇路径、Markdown 大小与 SHA-256、任务状态和缺失图片。
全部通过返回 `0`；缺失、额外 Markdown、空正文或未完成任务返回 `1`。

## 实测结果（2026-11-03）

`--year 2024 --limit 3` 已实际调用精准 API / vlm 完成：

| PDF | 页数 | Markdown 字节数 |
| --- | ---: | ---: |
| -index and its variants… | 26 | 87,176 |
| A Deep Multi-Tasking Approach… | 17 | 44,479 |
| A call for transparency in gender assignment approaches | 4 | 11,588 |

3 篇均生成非空正文，Markdown 中共 10 处图片引用全部可在本地找到。
重新执行同一命令已确认 3 篇均跳过，无重复上传或解析。
检查涵盖输出结构、正文存在、图片引用与续跑行为，未逐页人工校对识别准确率。

### 2024 年全量转换核验

2024 年目录的 291 个 PDF 已全部使用精准 API / vlm 转换成功，最终结果：

- PDF：291 个；Markdown：291 个；逐篇对应有效：291 篇。
- 失败：0；未完成：0；额外 Markdown：0。
- Markdown 均为非空 UTF-8，图片引用检查通过，共 2948 处。
- 转换完成后，291 个源文件与保存任务的指纹一致。
- 详细逐篇记录：[mineru_2024_verification.json](mineru_2024_verification.json)。

运行中遇到服务端长时间排队，已补充 `--watch` 轮询批次的能力，并通过 8 项离线回归测试。
可按以下命令持续执行其他年份或全量数据：

```bash
bash data_get/run_mineru.sh --batch-size 50 --max-wait 20 --watch
```

已经完成的 2024 年文件会自动跳过。

### 2023 年两篇失败文件的恢复

两篇原任务均返回 `parsing failed, please try again later`，接口未提供更细的内部错误。
本地检查确认分别为 22 页和 24 页，未加密，每页文本可读，大小和页数均未超限。
保留原始 PDF 和精准 `vlm` 模型，分别重新提交后两篇均成功。

最终 2023 年核验：323 个 PDF 对应 323 个有效 Markdown，0 失败、0 遗漏，2895 处图片引用检查通过。
报告：[mineru_2023_verification.json](mineru_2023_verification.json)；
原失败任务、文件检查和恢复结果：[mineru_2023_retry_diagnosis.json](mineru_2023_retry_diagnosis.json)。

脚本已补充临时解析失败的自动重试上限与失败历史保存；11 项离线回归测试通过。
