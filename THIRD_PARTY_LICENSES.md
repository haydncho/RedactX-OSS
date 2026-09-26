# 第三方组件许可证

RedactX 运行时依赖以下开源组件。它们各自的许可证以其官方仓库为准，此表仅便于查阅。

| 组件 | 用途 | 许可证 |
|---|---|---|
| RapidOCR（含 PP-OCRv6 模型） | OCR | Apache-2.0 |
| ONNX Runtime | 模型推理 | MIT |
| pypdfium2 / PDFium | PDF 渲染与文字层 | Apache-2.0 / BSD-3-Clause |
| pikepdf / qpdf | PDF 清理、计页 | MPL-2.0 / Apache-2.0 |
| img2pdf | 图片封装为 PDF | LGPL-3.0（仅作为依赖调用，未修改源码） |
| OpenCV（headless） | 图像处理 | Apache-2.0 |
| NumPy | 数组计算 | BSD-3-Clause |
| Pillow | 图像读写与文字绘制 | MIT-CMU（HPND） |
| RapidFuzz | 模糊匹配 | MIT |
| Python-Markdown | Markdown 转换 | BSD-3-Clause |
| FastAPI / Starlette | 接口服务 | MIT / BSD-3-Clause |
| Uvicorn | ASGI 服务器 | BSD-3-Clause |
| python-multipart | 文件上传解析 | Apache-2.0 |
| LibreOffice（外部程序，可选） | Word / WPS 等文档转 PDF | MPL-2.0 |
| Noto Sans CJK（容器镜像内） | 打码标签字体 | OFL-1.1 |
| fontTools（仅评估工具 `bench/`） | 合成 PDF 的字体子集化 | MIT |

评估工具生成合成病案时调用本机系统字体（macOS 自带的宋体、黑体与手写风格字体），字体文件不随仓库分发，生成的 PDF 只在本地使用、不入库。

刻意未使用的组件：PyMuPDF（AGPL-3.0）、Ultralytics YOLO（AGPL-3.0）、InsightFace 预训练模型（非商用）、MinIO（AGPL-3.0）。新增依赖前请先确认许可证与 Apache-2.0 兼容。
