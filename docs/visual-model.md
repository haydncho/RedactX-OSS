# 印章、签名视觉检测

RF-DETR 为原有规则增加视觉检测；默认关闭。印章颜色、字段锚定等规则仍生效。
在去水印之前检测所有原始页面（包括带文字层 PDF），在实际生成的输出文件上检查残留。
未检出不等于保证没有隐私，必须结合复核与业务样本评估。

## 模型来源与许可

迁移自 drg-oras 的 visual_detector.py，采用
[bluecopa/rf-detr-stamp-signature-detector](https://huggingface.co/bluecopa/rf-detr-stamp-signature-detector/tree/c59fd4f451b254501700a56c7769f1a3d788c753)，
固定修订 c59fd4f451b254501700a56c7769f1a3d788c753。模型卡声明 Apache-2.0；
导出工具 RF-DETR 1.2.1（Apache-2.0）。训练数据授权及适用范围以上游声明为准。
不分发公开评估图片，不将模型卡指标解释为本项目病案准确率。

已验证的导出包（三个文件）：

| 文件 | SHA-256 |
| --- | --- |
| model.onnx | 5855eb13aa9614051e176479a23daa8db552c9810094f26e8ce4ba26cac9d6d9 |
| config.json | f66e7017ec854750db525ccaea6db96d740158b4ae93f4d8c86d65a677c0758a |

manifest.json 必须列出这两个摘要。加载时同时比对固定可信摘要与实际字节，
不能通过同时替换模型和清单绕过固定版本。换模型须更新适配器并重新验收。
上游 checkpoint SHA-256 为 3a831ae093be64a01a4d9c635dc6071ad7c43c3e95c3da0b33a17351df1e7909。
导出时已做严格参数加载和 PyTorch/ONNX 数值对照；服务运行不需要 PyTorch，也不联网下载。
独立复现导出使用 deploy/export_visual_model.py；source 目录须包含已核验的 checkpoint.pth。
导出环境依赖 rfdetr 1.2.1、torch 2.14.0、torchvision 0.29.0、transformers 4.55.4、
peft 0.17.1、onnx 和 onnxruntime，与运行环境分开管理。

输入为 RGB、560×560、ImageNet 均值方差归一化；输出按名称读取 boxes/logits。
stamp/signature 映射 SEAL/SIGNATURE，忽略复选框。长边超过1000像素时运行整页及四个65%重叠分块，
同类 IoU>0.45 去重，原件阈值0.35、最终检查0.25，原件框外扩8%且至少4像素。
0.3.0 修复更新后，每个 query 取四类最高分，复选框最高时排除；跨类 IoU>0.8 去重。
模型区域存在闭合双环椭圆章的正向形状证据时纠正为 SEAL，同章边缘弧段归入印章。
没有形状证据时保留模型类别，不能据此保证所有印章、签名的分类正确。
规则框与模型框合并后，报告 sources 保留各检测来源，source 字段保持兼容。
推理在进程内串行，限制页线程并发带来的模型内存峰值。模型及配置只读挂载。

## 配置与部署

| 环境变量 | 默认 | 含义 |
| --- | --- | --- |
| REDACTX_VISUAL_ENABLED | 0 | 设为1才启用新模型 |
| REDACTX_VISUAL_MODEL_DIR | models/visual | 与旧 models/detector 分开的导出包目录 |
| REDACTX_VISUAL_THREADS | 2 | 模型 CPU 线程数 |

模型不进 Git。Apple Container 的 run.sh/dev.sh 在启用时校验必需文件并只读挂载。
Docker Compose 用 REDACTX_VISUAL_HOST_DIR 指定宿主目录，默认 ../../models/visual。
启用后缺模型、哈希错误或推理失败会使自动任务失败，不静默降级为无视觉检测。
关闭新模型仍保留旧 RT-DETR 的配置和行为。

独立验收使用 deploy/container/visual-test.sh：基于现有 redactx:0.2.8 构建
redactx:visual-test，容器 redactx-visual-test，地址 http://127.0.0.1:8091/app，
6GB/6CPU、单页并行，数据和模型位于 local/visual-acceptance/，不挂外网隧道。
脚本不替换同名容器，不调用生产 run.sh，不修改生产数据。
停止测试用 container stop redactx-visual-test；正式升级需另行确认。

## 检查证据与复核

报告新增 visual_verification：status、pages、findings、model_version，
完成检查还包含 threshold、entities。findings 是最终文件中的疑似残留位置（归一化框），不是自动遮盖框。

| status | 含义 |
| --- | --- |
| disabled | 服务未启用新模型 |
| not_applicable | 未选择 SEAL/SIGNATURE |
| passed | 所选类型检查完成、未检出残留；不是绝对无隐私保证 |
| review | 检出疑似残留，需人工复核，不循环自动扩框 |
| stale | 文件开始改动，旧检查证据已失效 |
| failed | 人工改框已保存，但重新视觉检查失败，需人工复核 |

人工改框前先使旧证据失效，重建后重新检查；旧文字自检也标记 stale、passed=null，
避免保留对旧像素的通过结论。健康接口新增 visual.enabled/ready/version。
同步脱敏响应增加 X-Redact-Visual-Status 和 X-Redact-Visual-Findings。
X-Redact-Visual-Concerns 为原件日期冲突疑点数；状态 passed 只代表最终文件残留检查，不能忽略原件疑点。
网页展示检查状态，并用橙色虚框提示残留位置；复核后重新显示最新证据。
原件模型框与 OCR 日期相交时不自动遮盖，改在 visual_concerns 中记录 DATE_OVERLAP；
界面同样显示原件疑点框。该提示是日期保护策略，需人工确认，不计入最终文件的残留框。
灰色合成章的原始模型输出可能为 SIGNATURE；修复更新为有双环证据的圆章增加类别纠正。
破损章、矩形章及复杂重叠仍可能混淆，区域定位与印章/签名类别准确性必须分别评估。
这不包含 drg-oras 的评分、模板资格和业务放行机制。

## 可复现评估

先准备已有的 bench/out 合成评估集，再运行：

```bash
.venv/bin/python -m bench.visual_acceptance --model local/visual-acceptance/models
```

在相同本机环境中用独立子进程对比基线和新模型，覆盖文字层、扫描、旋转、混合、
淡章、水印、低质量、照片和护理签名页。JSON 记录覆盖、误遮、多遮、耗时及进程峰值 RSS。
这些是虚构材料回归结果；真实病案仍需获得授权的本地人工评估。
