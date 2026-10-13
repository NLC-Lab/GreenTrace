# API 调用示例（curl 全流程）

以默认配置启动服务：

```bash
export GREENTRACE_DATA_DIR=./data
export GREENTRACE_API_KEY=local-demo-secret      # 可选；不设置则不加鉴权
python -m uvicorn greentrace.api:app --host 127.0.0.1 --port 8000
```

下面所有请求都带上 `-H "X-API-Key: local-demo-secret"`（仅当设置了 `GREENTRACE_API_KEY`；
`/health` 永远豁免鉴权）。示例用 `jq` 便于阅读，没有 `jq` 可直接去掉尾部管道。

> 交易必须用 Ed25519 私钥签名。命令行里最容易的做法是用仓库自带的 Python 物体生成交易 JSON，
> 见文末「用 Python 生成签名交易」。

---

## 0. 探活与节点信息

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","height":0}

curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/nodes
# {"validators":["node-1",...,"node-4"],"fault_tolerance":1,"quorum":3,"local_address":"..."}
```

## 1. 治理：建组织、加成员（必须签名）

```bash
# 由创世管理员（data/node-1.identity 对应的地址）签名的治理交易
curl -s -X POST http://127.0.0.1:8000/governance/transactions \
  -H "Content-Type: application/json" -H "X-API-Key: local-demo-secret" \
  -d @gov_org.json
# {"accepted":true,"tx_hash":"...","height":0,"status":"committed"}

curl -s -X POST http://127.0.0.1:8000/governance/transactions \
  -H "Content-Type: application/json" -H "X-API-Key: local-demo-secret" \
  -d @gov_member.json
```

常见错误：

| 状态码 | detail | 含义 |
|---|---|---|
| 403 | `governance changes require a signed governance transaction` | 走了无签名的旧接口 `/organizations*` |
| 422 | `action must start with governance_` | 往治理入口提交了业务动作 |
| 400 | `permission denied: governance_add_member` | 签名者不是 admin |

## 2. 碳数据闭环：登记 → 上报 → 审计

```bash
curl -s -X POST http://127.0.0.1:8000/transactions -H "X-API-Key: local-demo-secret" \
  -H "Content-Type: application/json" -d @register_batch.json     # action=register_batch
curl -s -X POST http://127.0.0.1:8000/carbon-data -H "X-API-Key: local-demo-secret" \
  -H "Content-Type: application/json" -d @submit_carbon.json      # 需 version + evidence_hash
curl -s -X POST http://127.0.0.1:8000/audit-confirmations -H "X-API-Key: local-demo-secret" \
  -H "Content-Type: application/json" -d @audit_carbon.json       # 审计方不能自审
```

业务规则（写错会返回 400，detail 说明原因）：

- `version` 不能为空且不能重复；已批准版本不可替换；
- 提供 `evidence` 时，`evidence_hash` 必须等于 `canonical_hash(evidence)`；
- 已批准（未撤稿）的批次不能再被审计改写；
- 只有批次碳数据归属方（`owner`）能提交碳数据。

## 3. 凭证流转：转移 / 冻结 / 撤销 / 退休

```bash
curl -s -X POST http://127.0.0.1:8000/transfers    -d @transfer_credits.json   # 只能动自己持有的额度
curl -s -X POST http://127.0.0.1:8000/retirements  -d @retire_credits.json     # 退休不可逆
curl -s -X POST http://127.0.0.1:8000/accounts/freeze -d @freeze_account.json  # regulator 权限
```

额度按持有人分账：`holdings` 记录每个人持有多少，`remaining_credits == sum(holdings)` 恒成立；
全部退休后批次状态变为 `retired`。

## 4. 产业链订单：创建 → 确认 → 交付 → 验收（自动结算）

```bash
curl -s -X POST http://127.0.0.1:8000/tokens/mint  -d @mint_tokens.json        # issuer 发币给买方
curl -s -X POST http://127.0.0.1:8000/supply-chain/orders -d @create_order.json
curl -s -X POST http://127.0.0.1:8000/supply-chain/orders/ORDER-001/actions -d @ack.json
curl -s -X POST http://127.0.0.1:8000/supply-chain/orders/ORDER-001/actions -d @deliver.json
curl -s -X POST http://127.0.0.1:8000/supply-chain/orders/ORDER-001/actions -d @accept.json
```

- 下单时买方余额进入 `escrow:ORDER-001`，验收通过才结算给供应商；
- 部分交付 / 部分验收需传 `amount`，累计不得超过订单量与已交付量；
- 争议需 `evidence_hash`，由 regulator 裁决（`refund` / `accept` / `partial_refund`）；
- 确认超时以**服务端时间**为准（不接受客户端传入的 `now`）。

## 5. 审计、追溯与完整性

```bash
curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/integrity
# {"valid":true,"state_consistent":true,"height":12,"tip":"..."}

curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/transactions/order-1
curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/orders/ORDER-001/timeline | jq '.[].stage'
curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/accounts/<address>/funds
curl -s -H "X-API-Key: local-demo-secret" "http://127.0.0.1:8000/chain?offset=0&limit=5"
curl -s -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/merkle-proof/0/0
```

`state_consistent` 会把当前内存状态与「从链确定性重放得到的状态」逐字段比对：
手工改过 `data/node-1.state.json`（改余额、账本、订单、角色、组织）后它会变成 `false`，
而 `valid` 仍为 `true`（链本身没被改）。

重建（灾备路径）：

```bash
curl -s -X POST -H "X-API-Key: local-demo-secret" http://127.0.0.1:8000/integrity/rebuild
# {"rebuilt":true,"valid":true,"state_consistent":true,...}
```

---

## 用 Python 生成签名交易

```python
from pathlib import Path
from greentrace.crypto import Identity
from greentrace.models import Transaction

admin = Identity.from_private_key(Path("data/node-1.identity").read_text().strip())
tx = Transaction.signed(admin, "gov-org-1", 1, "governance_register_organization",
                        {"organization_id": "org-1", "name": "Org 1"})
Path("gov_org.json").write_text(tx.model_dump_json(), encoding="utf-8")
```

`Transaction.signed()` 的第三个参数是**该发送者**的递增 nonce（从 1 开始，失败交易不消耗 nonce）。

---

## 验收清单（可当作演示前的自检）

| 检查 | 期望 |
|---|---|
| `GET /health` | `status: ok`，且不要求 API Key |
| 未设置 Key 时访问 `/chain` | 200（本地演示开放） |
| 设置 Key 后访问 `/chain` 不带头 | 401 |
| 同一 nonce 并发提交 N 笔不同 request_id | 只有 1 笔 committed，其余 400，无 5xx |
| 同一签名交易重复提交 | 返回同一回执，余额只扣一次 |
| 转账超过自己持有的额度 | 400，`credit transfer exceeds remaining balance` |
| 非 regulator 调用 `/accounts/freeze` | 400，`permission denied` |
| 手工改 `state.json` 的余额 | `state_consistent: false` |
| 手工改区块内容（不重算哈希） | `valid: false` |
| `POST /integrity/rebuild` | 200，且 `state_consistent: true` |
