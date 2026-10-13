## 这个 PR 做了什么

（一句话说明，关联 Issue 用 `Closes #123`）

## 变更类型

- [ ] 新业务能力
- [ ] 工程/重构
- [ ] 文档
- [ ] CI / 部署

## 自检清单

- [ ] `python -m pytest -q` 通过
- [ ] `python -m ruff check greentrace tests` 通过
- [ ] `python -m mypy greentrace` 通过
- [ ] 新增动作已补：权限角色、状态机约束、幂等（`request_id`/nonce）、失败路径与 trace 证据
- [ ] 未提交 `.env`、私钥、API Key、`data/` 或缓存目录
- [ ] 若改变了链上状态语义、接口或依赖，已同步更新 README / CHANGELOG / `constraints.txt`

## 已知限制

（这个 PR 还留下什么问题，或明确不打算处理的边界）
