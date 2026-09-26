# 参与贡献

感谢你愿意改进锐消 RedactX。

## 开发环境

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/uvicorn service.app:app --host 127.0.0.1 --port 8000
```

处理 Word、WPS 等文档需要本机安装 LibreOffice。

## 红线

1. **绝不提交真实数据。** 病案、证件、扫描件、处理结果、截图一律不进仓库，包括 issue 和 PR 描述。测试只使用虚构数据（见 `tests/`），需要样例文件时请用 `bench/` 生成合成病案（`python -m bench.make`）。
2. **不引入外部调用。** 处理过程必须完全离线，不调用任何外部模型或云端接口；Web 页不加载外部字体、脚本和样式。
3. **先擦除、后装饰。** 新增打码样式时，擦除步骤必须覆盖全部原像素，装饰函数只能读取擦除后的图像。
4. **日志不写原文。** 日志、报告、异常信息中不得出现识别出的敏感内容。
5. **日期不遮盖。** 这是项目的既定脱敏口径，规则层需把日期排除在外。

## 提交流程

- 从 `main` 拉分支，提交信息写清改了什么、为什么。
- 新增或修改识别规则时，同时补充正反例单元测试。
- 新增依赖需在 `THIRD_PARTY_LICENSES.md` 中登记许可证。
- 提交前确保 `pytest` 全部通过；改动识别或打码逻辑时，附上 `python -m bench.evaluate` 前后的遮全率与误遮对比。
