# 锐消 RedactX

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

病案等文档的本地脱敏服务。上传 PDF 或图片，自动识别患者与医护人员姓名、医院名称、证件号、电话、病案号、手写填写区、签名、印章等内容，按指定样式打码，输出栅格化重建后的 PDF。

- 全部在本机处理，不调用任何外部模型或云端接口。
- 日期一律不遮盖（含出生日期、入出院日期、手写日期）。
- 原件处理完立即删除；脱敏结果与预览按保留时长（默认 24 小时）自动清除。

## 运行

### 方式一：本机直接运行（开发）

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。

### 方式二：Apple container

```bash
deploy/container/run.sh 8080
```

首次运行会构建镜像（需要下载 Python 基础镜像与依赖，一次性）。数据目录挂载在 `data-container/`，服务只绑定 127.0.0.1。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/jobs` | 提交异步任务：`file`（文件）、`options`（JSON 字符串）、`retention_hours`、`password` |
| GET | `/v1/jobs/{id}` | 查询状态与进度 |
| GET | `/v1/jobs/{id}/result` | 下载脱敏文件 |
| GET | `/v1/jobs/{id}/report` | 打码报告：页码、类型、来源、归一化坐标，不含原文 |
| GET | `/v1/jobs/{id}/preview/{page}?v=before\|after` | 预览图 |
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
  "verify": false
}
```

样式：`label` 浅色标签、`background` 背景色擦除、`replace` 代号替换、`mosaic` 安全马赛克、`hatch` 斜线花纹、`inpaint` 图像修复、`black` 黑色块。所有样式都先擦除原像素再绘制外观。

设置环境变量 `REDACTX_API_KEY` 后，所有 `/v1` 接口需要请求头 `X-API-Key`。

## 配置

| 变量 | 默认 | 说明 |
|---|---|---|
| `REDACTX_DATA` | `data` | 数据目录 |
| `REDACTX_DPI` | `200` | 默认渲染分辨率 |
| `REDACTX_RETENTION_HOURS` | `24` | 结果默认保留时长 |
| `REDACTX_WORKERS` | `1` | 并行任务数（16 GB 内存建议 1） |
| `REDACTX_MAX_UPLOAD_MB` | `200` | 上传上限 |
| `REDACTX_API_KEY` | 无 | 设置后启用 API Key 校验 |

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
deploy/container/ Apple container 镜像与启动脚本
tests/            单元测试（全部使用虚构数据）
```

## 已知限制（第一版）

- 未接入 NER 模型：正文中出现、但从未在字段里出现过的人名可能漏遮。V2 上线 NER。
- 颜色很淡的印章、斜向水印中的院名暂不处理。
- 手写签名只按字段位置遮盖，没有专门的签名检测模型。

## 参与贡献

欢迎提交 issue 和 PR，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。**任何情况下都不要在仓库、issue 或 PR 中附带真实病案或个人信息。** 安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

## 许可证

[Apache License 2.0](LICENSE)。第三方组件许可证见 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
