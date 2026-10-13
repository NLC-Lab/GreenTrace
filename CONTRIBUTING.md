# 贡献指南

## 本地开发

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows；macOS/Linux 使用 source .venv/bin/activate
pip install -e ".[test]"
pytest -q
python -m compileall -q greentrace
```

## 提交要求

- 新增业务动作必须有权限、状态机、幂等和失败路径；
- 余额、凭证和订单变化必须留下审计或 trace 证据；
- 不提交 `.env`、私钥、API Key、`data/` 或生成缓存；
- 更新 README 中的 API 和能力边界；
- Pull Request 应说明测试命令和已知限制。

## 业务边界

CarbonCoin 和 GreenPoint 仅为本地演示记账单位。贡献不能把它们描述为法币、
证券、真实碳信用或可提现资产。
