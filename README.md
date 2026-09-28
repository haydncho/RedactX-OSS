# 锐消 RedactX

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**项目介绍页：[haydncho.github.io/RedactX-OSS](https://haydncho.github.io/RedactX-OSS/)**（截图、识别范围、工作原理、评估结果、部署步骤）

病案等文档的本地脱敏服务。上传 PDF 或图片，自动识别患者与医护人员姓名、医院名称、证件号、电话、病案号、手写填写区、签名、印章等内容，按指定样式打码，输出栅格化重建后的 PDF。

- 全部在本机处理，不调用任何外部模型或云端接口。
- 日期一律不遮盖（含出生日期、入出院日期、手写日期）。
- 上传的原文件处理完立即删除；脱敏结果、预览图（含供对比查看的原件缩小版，宽度不超过 1400 像素）按保留时长（默认 24 小时）自动清除，也可随时手动删除。

![结果对比：左侧选择脱敏字段与样式，中间拖动分隔线对比原件与脱敏后，右侧为遮盖统计](docs/images/screenshot-result.png)

<details>
<summary>更多截图：上传页、深色主题</summary>

![上传页](docs/images/screenshot-upload.png)

![深色主题](docs/images/screenshot-result-dark.png)

</details>

截图中的病案为评估集生成的合成数据，姓名、机构、号码均为虚构。

## 运行

### 方式一：本机直接运行（开发）

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
# 可选：正文人名识别模型（一次性联网下载并导出，约 100 MB，写入 models/ner/）
uv pip install --python .venv/bin/python -e ".[ner-export]" && .venv/bin/python deploy/ner_export.py
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。

### 方式二：Apple container

```bash
deploy/container/run.sh          # 默认端口 8090、容器名 redactx-oss；也可 run.sh <端口>
```

首次运行会构建镜像（需要下载 Python 基础镜像与依赖，一次性）。依赖按 `deploy/container/requirements.lock` 安装：版本固定、逐个核对 SHA-256，改了 `pyproject.toml` 的依赖后用下面的命令重新生成：

```bash
uv pip compile pyproject.toml --generate-hashes --python-version 3.12 --python-platform aarch64-unknown-linux-gnu \
  --no-emit-package opencv-python --no-header -o deploy/container/requirements.lock
```

容器以非 root 用户运行，根文件系统只读（可写的只有数据卷与 `/tmp`），不带任何 Linux 能力；Word 等文档在无网络、看不到数据目录的沙箱里转换。人名识别、检测模型加载前按目录里的 `SHA256SUMS` 核对，文件被改动时报错而不是悄悄少遮（导出、训练脚本会写入这个清单）。

开发时不必每次重建镜像：

```bash
deploy/container/dev.sh              # 用已有镜像的运行环境，挂载仓库里的代码、页面与模型，改动即时生效
deploy/container/dev.sh --rebuild    # 依赖（pyproject.toml、Containerfile）变了时才需要
```

Python 代码改动后服务自动重启（正在处理的任务会中断），Web 页刷新浏览器即可。容器名与端口同 `run.sh`，两者互相替换；最终部署用 `run.sh` 按版本号生成镜像。构建前须先按方式一导出正文人名识别模型到 `models/ner/`，镜像会把它一并打包。数据目录挂载在 `data-container/`，服务只绑定 127.0.0.1。

### 方式三：Docker

Linux 服务器或装了 Docker 的机器上用 Docker Compose（v2）。镜像与方式二共用 `deploy/container/Containerfile`，依赖锁文件同时适用于 x86_64 与 arm64。

```bash
# 1. 导出正文人名识别模型（一次性，见方式一），镜像会把 models/ner/ 一并打包
# 2. 配置：管理员 Key、端口、并行页数等
cp deploy/docker/.env.example deploy/docker/.env
# 3. 构建并启动
docker compose -f deploy/docker/compose.yaml up -d --build
# 4. 给使用者生成用户 Key（自动识别 Docker）
deploy/keys.sh keys create "演示-张医生"
```

打开 http://127.0.0.1:8090 。与方式二相同的加固：只绑定 127.0.0.1、非 root 用户、根文件系统只读（可写的只有数据卷与 `/tmp`）、去掉全部 Linux 能力，另加 `no-new-privileges`。数据放在命名卷 `redactx_redactx-data` 里（继承镜像里 `/data` 的属主；挂载宿主机目录会因属主不对写不进去）。升级：`git pull` 后再执行第 3 步；停止：`docker compose -f deploy/docker/compose.yaml down`（数据卷保留，加 `-v` 连数据一起删）。

`.dockerignore` 把 `example/`、`local/`、各数据目录、`.env` 与所有 PDF、扫描件、Office 文档排除在构建上下文之外，病案样本不会被发送给构建进程。

Word 等文档的转换沙箱需要在容器内创建用户命名空间，Docker 默认的 seccomp 配置通常不允许，服务检测到后会退回直接运行 LibreOffice 并记一条警告（Apple container 下沙箱可用）。要求必须有沙箱时在 `.env` 里设 `REDACTX_REQUIRE_SANDBOX=1`：没有沙箱时拒绝转换 Word 等文档，PDF 与图片照常处理。

## 示例数据

试用、演示或联调用的示例文档，全部为虚构数据（姓名、机构、号码随机生成），可以放心上传：

```bash
uv pip install --python .venv/bin/python -e ".[bench]"
.venv/bin/python -m bench.samples                 # 写到 samples/（已加入 .gitignore），约 15 秒
.venv/bin/python -m bench.samples --quick --truth # 跳过两份长文档；另存标准答案（应遮、应留的位置与类型）
```

生成两份 30 多页的住院全套材料（系统导出 38 页、扫描件 37 页：病案首页、入院记录、病程、手术与麻醉记录、医嘱、体温单、护理记录、检验检查、病理、出院记录、诊断证明、处方，以及费用明细清单、医保结算清单、收费电子票据、预交金与出院结算单，金额前后一致），各种形态的短病案（系统导出、带水印、白纸扫描、手机拍照、难点扫描、患者信息登记表），以及 Markdown、纯文本各一份。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/jobs` | 提交异步任务：`file`（文件）、`options`（JSON 字符串）、`retention_hours`、`password` |
| GET | `/v1/jobs/{id}` | 查询状态与进度 |
| GET | `/v1/jobs/{id}/result` | 下载脱敏文件 |
| GET | `/v1/jobs/{id}/report` | 打码报告：页码、类型、来源、归一化坐标，不含原文 |
| GET | `/v1/jobs/{id}/preview/{page}?v=before\|after` | 预览图 |
| PUT | `/v1/jobs/{id}/review` | 提交复核后的全部遮盖框，重新打码受影响的页并重建输出 |
| POST | `/v1/jobs/{id}/review/finish` | 复核完成，删除为复核保留的打码前页面 |
| DELETE | `/v1/jobs/{id}` | 立即删除结果与预览 |
| POST | `/v1/redact` | 同步脱敏，限 10 页、20 MB，直接返回文件 |
| GET | `/v1/catalog` | 实体类型、样式、预设 |

`options` 示例：

```json
{
  "entities": ["PERSON", "STAFF", "ORG", "ID_CARD", "PHONE", "MEDICAL_ID", "HANDWRITTEN_FIELD", "SIGNATURE", "SEAL", "LOGO"],
  "default_style": "label",
  "styles": {"SIGNATURE": "hatch", "SEAL": "background"},
  "custom_words": ["某某医院东院区"],
  "mode": "strict",
  "label_text": "type",
  "dpi": 200,
  "verify": "auto",
  "keep_source": false
}
```

样式：`label` 浅色标签、`background` 背景色擦除、`replace` 代号替换、`mosaic` 安全马赛克、`hatch` 斜线花纹、`inpaint` 图像修复、`black` 黑色块。所有样式都先擦除原像素再绘制外观。

**复核**：在 Web 页查看结果时点“复核”，可以在页面上拖出新框补遮漏掉的内容，保存后重新打码并重建输出。默认不保留全尺寸的打码前页面，已打码的像素无法恢复，所以只能加框。提交时打开“保留原件以便复核”（`keep_source`），会另存打码前的页面，复核时还可以删框、改框；点“完成复核”或任务到期时删除。

**标注导出**：服务端设置 `REDACTX_ALLOW_EXPORT=1` 时，保留原件、尚未完成复核的任务可在复核台“导出标注”（`GET /v1/jobs/{id}/export`）：打码前的原始页面与复核后的全部框，COCO 格式，供训练手写区、签名检测模型。导出含真实病案内容，开启时必须同时设置 `REDACTX_API_KEY`，否则导出接口拒绝服务；每次导出记入数据目录下的 `export-audit.log`（时间、任务、页数、框数，不含内容）。只在授权的标注环境里打开，平时不要设置。标注流程与规则见 [docs/annotation.md](docs/annotation.md)。

设置环境变量 `REDACTX_API_KEY` 后，所有 `/v1` 接口需要请求头 `X-API-Key`。

**按 Key 隔离任务**：`REDACTX_API_KEY` 是管理员 Key，能看到、操作全部任务。给每位使用者单独生成用户 Key，每个 Key 只能看到、操作自己提交的任务，访问别人的任务一律返回 404；标注导出只有管理员 Key 可用。用户 Key 由后台脚本生成，库里只存哈希，明文只显示一次，生成时保证不与库里已有的（含已吊销的）和管理员 Key 重复：

```bash
deploy/keys.sh keys create "演示-张医生"      # 容器部署；本机直接运行用 python -m service.admin，参数相同
deploy/keys.sh keys list                      # 名称、前缀、创建与最近使用时间、任务数
deploy/keys.sh keys revoke key_xxxxxxxxxxxx   # 立即失效；加 --purge 同时删除它提交的任务
deploy/keys.sh jobs clear --yes               # 清空全部任务
```

同一来源 10 分钟内输错 Key 20 次后暂时拒绝（429）。既没设管理员 Key、也没生成用户 Key 时为本机模式，不需要 Key。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `REDACTX_DATA` | `data` | 数据目录 |
| `REDACTX_DPI` | `200` | 默认渲染分辨率 |
| `REDACTX_RETENTION_HOURS` | `24` | 结果默认保留时长 |
| `REDACTX_WORKERS` | `1` | 并行任务数（16 GB 内存建议 1） |
| `REDACTX_PAGE_WORKERS` | `2` | 单个任务内同时打码、自检的页数；每多一页内存峰值约多 0.5 GB，设为 1 时最省内存 |
| `REDACTX_MAX_UPLOAD_MB` | `50` | 单个文件上传上限（MB） |
| `REDACTX_API_KEY` | 无 | 管理员 Key，设置后启用 API Key 校验；用户 Key 用 `deploy/keys.sh` 生成 |
| `REDACTX_REQUIRE_SANDBOX` | 无 | 设为 `1` 时，无法为文档转换创建沙箱就拒绝转换 Word 等文档（默认退回直接运行并记警告） |

## 目录

```
redactx/          识别与打码引擎
  ingest.py       文件校验、整页渲染、文字层与图片对象
  ocr.py          PP-OCRv6（RapidOCR / ONNX Runtime），单字坐标、整页方向纠正
  detect/         规则与校验位、字段锚定、词表、文档内实体传播
  vision.py       红章、二维码条码、填写区笔迹、图片对象归类
  redact.py       打码样式引擎（先擦除、后装饰）
  pipeline.py     两遍处理流水线与栅格化重建
service/          FastAPI 接口与任务管理
web/              Web 页（纯静态，不加载外部资源）
deploy/container/ 镜像（Containerfile、依赖锁文件）与 Apple container 启动脚本
deploy/docker/    Docker Compose 部署
deploy/keys.sh    后台管理：生成、吊销用户 API Key，清空任务
tests/            单元测试（全部使用虚构数据）
bench/            合成评估集（虚构病案、遮全率、误遮、耗时与性能）与示例数据（bench.samples）
```

## 评估

`bench/` 生成全部为虚构数据的合成病案，并在上面跑完整流水线打分。需要 macOS 自带的中文字体（其他系统可用 `REDACTX_BENCH_FONT` 指定一个 TrueType 中文字体）。

```bash
uv pip install --python .venv/bin/python -e ".[dev,bench]"
.venv/bin/python -m bench.make --out bench/out --cases 3      # 生成：每种形态 3 份，每份 4 页
.venv/bin/python -m bench.evaluate --data bench/out            # 评估：结果写入 bench/out/results/
.venv/bin/python -m bench.prose                                 # 正文人名（纯文本）：遮全率与误遮
.venv/bin/python -m bench.show bench/out/docs/scan-001.pdf --page 1 --out page1.png   # 查看标准答案框
```

十三种形态（`harsh`、`photo` 为加难形态，`idtext`、`idscan` 检验覆盖面，见下）：`text` 系统导出（文字层、电子签名小图、跨页重复 Logo），`textwm` 系统导出加文字层院名水印，`scan` 白纸扫描 200 DPI（手写姓名与签名、红章压字、轻微歪斜），`kraft` 牛皮纸扫描 300 DPI，`rotated` 横放扫描（内容旋转 90°），`mixed` 文字层页与扫描页混排，`hard` 扫描件加淡粉色印章、平铺斜向水印与大号连笔签名，`mrc` 复印机 MRC 分层压缩。扫描类形态另有一页手写日常病程（手写日期与诊断、各种写法的医师签名）。加难形态：`harsh` 150 DPI 低质量扫描（重噪点、模糊、强 JPEG 压缩、歪斜可达 2.5°），`photo` 手机拍照（透视变形、不均匀光照与阴影）；两者另有第 7 页护理记录单（表格格子里的护士签名、一格两人签名、手写记录里的家属姓名、压在签名上的科室章）。覆盖面形态：`idtext`、`idscan` 只有一页患者信息登记表（护照、港澳通行证、军官证、医保卡号、分组书写的银行卡号、带分机的座机、+86 手机、邮箱、微信号、车牌、少数民族姓名、复姓、英文姓名），分别为文字层与白纸扫描。
标准答案分“应遮”（姓名、医护、机构、证件号、电话、医疗标识号、地址、签名、印章、Logo、二维码，以及只在正文出现的人名）与“应留”（日期、诊断与编码、检验结果、性别、年龄、费用、临床照片）。
指标：应遮项被覆盖 ≥ 80% 记为遮全；应留项被覆盖超过 15% 记为误遮；与任何应遮项都不重叠的遮盖区域记为多遮。

## 性能

`bench/perf.py` 用合成病案拼出约 100 页的文档，每份在独立进程中处理，测总耗时、每页耗时、首页耗时（含模型加载）与内存峰值：

```bash
.venv/bin/python -m bench.perf --out bench/perf --pages 100   # 结果写入 bench/perf/perf.md
```

Apple M4、16 GB，本机直接运行（纯 CPU，默认选项：扫描页做出厂自检，文字层页不做；第二遍同时处理 2 页）：

| 文档 | 页数 | 总耗时 | 每页 | 首页 | 内存峰值 |
|---|---|---|---|---|---|
| 系统导出（文字层） | 100 | 17 秒 | 0.17 秒 | 0.7 秒 | 1.0 GB |
| 白纸扫描 200 DPI（含手写页） | 102 | 188 秒 | 1.84 秒 | 1.5 秒 | 3.1 GB |

`REDACTX_PAGE_WORKERS=1`（不并行）时扫描件 2.07 秒/页、内存峰值 2.7 GB，文字层 0.19 秒/页。验收目标（容器内 6 核 6 GB 同样适用）：

| 指标 | 文字层 | 扫描件 |
|---|---|---|
| 每页 | ≤ 0.5 秒 | ≤ 3 秒 |
| 100 页文档 | ≤ 1 分钟 | ≤ 5 分钟 |
| 首页出结果 | ≤ 5 秒 | ≤ 5 秒 |
| 内存峰值 | ≤ 4 GB | ≤ 4 GB |

## 表单模板

OCR 没认出某个字段名时，可按本单位表单的模板推算它的位置，照常遮盖其右侧的内容。模板用空白或样例表单一条命令生成，见 [templates/README.md](templates/README.md)。
合成评估（`bench.template_eval`，扫描件病案首页上随机让 30% 的字段名认不出）：印刷字段内容遮全率 88.2% → 95.6%。

## 第三方模型

- 文字识别：RapidOCR 随包附带的 PP-OCR 模型（Apache-2.0）。
- 正文人名识别：[shibing624/bert4ner-base-chinese](https://huggingface.co/shibing624/bert4ner-base-chinese)（Apache-2.0），由 `deploy/ner_export.py` 固定版本下载、导出为 ONNX 并做 int8 量化，运行时只用 onnxruntime 与 tokenizers，不联网。未导出模型时该功能自动关闭，其余功能不受影响。

## 训练模型

`train/` 下是两套训练流程，训练依赖用 `uv pip install --python .venv/bin/python -e ".[train]"` 安装，运行时不需要：

- 签名检测（`train/detector.py`）：在 RT-DETR（PekingU/rtdetr_r18vd，Apache-2.0）上微调。训练数据为 COCO 格式，可以是合成数据（`python -m bench.to_coco`），也可以是复核台导出的真实标注。导出的 ONNX 放到 `models/detector/`（或 `REDACTX_DETECTOR_DIR`）后启用；默认没有模型，不启用。
- 正文人名（`train/ner.py`）：在现有 NER 模型上继续训练，语料为 JSONL。导出后用 `REDACTX_NER_DIR` 指向新模型，跑 `bench.prose` 与 `bench.evaluate` 比较，确认更好再替换。

第一版签名检测模型只用合成数据训练（1283 页，7 轮）。在留出的合成评估集上，签名 276/276 全部找到，没有多余的框；接入后低质量扫描、牛皮纸形态的遮全率升到 100%，误遮、多遮仍为 0。评估集与训练集出自同一个生成器，真实签名上不会这么理想，须用真实标注重训、确认不增加误遮后再启用。

## 已知限制

- 正文人名识别（NER）用通用语料（人民日报等）训练的模型，未在病历语料上微调；少见姓氏（如“向”“万”“付”）的人名偶有漏识别。只有姓氏的称呼（“李主任”）不遮。
- 压在照片等彩色图像上的水印分不出来，不会消除。
- 手写签名靠字段标签、日期位置与笔画连通来定位，没有专门的签名检测模型；既无标签、又不在日期后面的签名可能漏遮。

## 参与贡献

欢迎提交 issue 和 PR，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。**任何情况下都不要在仓库、issue 或 PR 中附带真实病案或个人信息。** 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

[Apache License 2.0](LICENSE)。第三方组件许可证见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
