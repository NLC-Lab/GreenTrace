"""FastAPI 集成测试：覆盖健康检查、节点信息、完整签名交易流、状态一致性与鉴权。

必须在 import ``greentrace.api`` 之前设置 ``GREENTRACE_DATA_DIR`` 到临时目录，
避免 API 模块的全局 ``_node`` 把数据写到仓库目录。
"""
import os
import tempfile

os.environ["GREENTRACE_DATA_DIR"] = tempfile.mkdtemp(prefix="greentrace-api-test-")
os.environ.pop("GREENTRACE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from greentrace.api import app, _node  # noqa: E402
from greentrace.crypto import Identity  # noqa: E402
from greentrace.models import Transaction  # noqa: E402

client = TestClient(app)


def _post_tx(identity, request_id, nonce, action, payload, endpoint="/transactions"):
    body = Transaction.signed(identity, request_id, nonce, action, payload).model_dump()
    return client.post(endpoint, json=body)


def test_health_and_nodes_are_public():
    assert client.get("/health").json()["status"] == "ok"
    nodes = client.get("/nodes").json()
    assert set(nodes["validators"]) == {"node-1", "node-2", "node-3", "node-4"}
    assert nodes["quorum"] == 3


def test_end_to_end_governance_and_batch_flow():
    admin = _node.identity
    producer = Identity()

    # 治理：通过签名交易建立组织与成员角色
    r1 = _post_tx(admin, "api-org-1", 1, "governance_register_organization",
                  {"organization_id": "api-org", "name": "API Org"},
                  "/governance/transactions")
    assert r1.status_code == 200, r1.text
    r2 = _post_tx(admin, "api-member-1", 2, "governance_add_member",
                  {"organization_id": "api-org", "address": producer.address,
                   "role": "enterprise"},
                  "/governance/transactions")
    assert r2.status_code == 200, r2.text

    # 企业登记批次
    r3 = _post_tx(producer, "api-batch-1", 1, "register_batch",
                  {"batch_id": "API-BATCH", "carbon_kg": "5.000"})
    assert r3.status_code == 200, r3.text
    assert r3.json()["status"] == "committed"

    # 查询批次可读
    batches = client.get("/batches").json()
    assert any(b["batch_id"] == "API-BATCH" for b in batches)

    # 完整性：链有效，且 live 状态与链重放一致
    integrity = client.get("/integrity").json()
    assert integrity["valid"] is True
    assert integrity["state_consistent"] is True


def test_integrity_detects_tampered_state():
    # 直接篡改内存余额（等价于改 state.json），state_consistent 应变为 False。
    _node.contract.balances["hacked-address"] = {"CarbonCoin": 999999}
    try:
        integrity = client.get("/integrity").json()
        assert integrity["state_consistent"] is False
    finally:
        _node.contract.balances.pop("hacked-address", None)


def test_api_key_protects_queries(monkeypatch):
    monkeypatch.setenv("GREENTRACE_API_KEY", "secret-key")
    assert client.get("/chain").status_code == 401
    assert client.get("/chain", headers={"X-API-Key": "secret-key"}).status_code == 200
    # /health 豁免鉴权，供探活
    assert client.get("/health").status_code == 200
