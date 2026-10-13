"""HTTP 层并发与幂等回归。

覆盖两类只有走完 API 才会暴露的问题：

1. **同一 nonce 的并发提交**：必须只有 1 笔落块，其余被 nonce 校验拒绝，且不出现 5xx
   （早期版本会出现多笔同 nonce 落块 + 链断裂 + 500）。
2. **同一签名交易重复提交**：必须返回同一回执，余额只扣一次。

在 import ``greentrace.api`` 之前把 ``GREENTRACE_DATA_DIR`` 指到临时目录，
避免全局 ``_node`` 把数据写进仓库目录。
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

os.environ["GREENTRACE_DATA_DIR"] = tempfile.mkdtemp(prefix="greentrace-http-test-")
os.environ.pop("GREENTRACE_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from greentrace.api import _node, app  # noqa: E402
from greentrace.crypto import Identity  # noqa: E402
from greentrace.models import Transaction  # noqa: E402

client = TestClient(app)
ADMIN = _node.identity


def _headers() -> dict[str, str]:
    key = os.environ.get("GREENTRACE_API_KEY")
    return {"X-API-Key": key} if key else {}


def _next_nonce(address: str) -> int:
    """读取节点当前的 nonce 计数，保证测试之间互不影响。"""
    return _node.nonces.get(address, 0) + 1


def _post(tx: Transaction, endpoint: str = "/transactions"):
    return client.post(endpoint, json=tx.model_dump(), headers=_headers())


def _governance(action: str, payload: dict, tag: str) -> None:
    tx = Transaction.signed(ADMIN, f"http-conc-{tag}", _next_nonce(ADMIN.address), action, payload)
    resp = _post(tx, "/governance/transactions")
    assert resp.status_code == 200, resp.text


def test_parallel_same_nonce_requests_commit_exactly_once():
    tag = "race"
    org = f"http-conc-org-{tag}"
    issuer, buyer = Identity(), Identity()

    _governance("governance_register_organization", {"organization_id": org, "name": "HTTP conc"}, tag)
    _governance("governance_add_member",
                {"organization_id": org, "address": issuer.address, "role": "issuer"}, f"{tag}-issuer")
    _governance("governance_add_member",
                {"organization_id": org, "address": buyer.address, "role": "buyer"}, f"{tag}-buyer")

    mint = Transaction.signed(issuer, f"{tag}-mint", 1, "mint_tokens",
                              {"to": buyer.address, "amount": 1000})
    assert _post(mint).status_code == 200

    nonce = _next_nonce(buyer.address)
    attempts = [
        Transaction.signed(buyer, f"{tag}-spend-{index}", nonce, "transfer_tokens",
                           {"to": issuer.address, "amount": 100})
        for index in range(12)
    ]

    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(_post, attempts))

    codes = [resp.status_code for resp in responses]
    assert codes.count(200) == 1, f"同一 nonce 只允许一笔成功，实际 {codes}"
    assert all(code < 500 for code in codes), f"并发下不应出现 5xx：{codes}"
    assert codes.count(400) == 11

    balance = client.get(f"/accounts/{buyer.address}", headers=_headers()).json()["balances"]
    assert balance["CarbonCoin"] == 900, f"余额应只扣一次：{balance}"

    integrity = client.get("/integrity", headers=_headers()).json()
    assert integrity["valid"] is True
    assert integrity["state_consistent"] is True


def test_identical_signed_transaction_is_idempotent():
    tag = "idem"
    org = f"http-conc-org-{tag}"
    issuer, buyer = Identity(), Identity()

    _governance("governance_register_organization", {"organization_id": org, "name": "HTTP idem"}, tag)
    _governance("governance_add_member",
                {"organization_id": org, "address": issuer.address, "role": "issuer"}, f"{tag}-issuer")
    _governance("governance_add_member",
                {"organization_id": org, "address": buyer.address, "role": "buyer"}, f"{tag}-buyer")

    assert _post(Transaction.signed(issuer, f"{tag}-mint", 1, "mint_tokens",
                                    {"to": buyer.address, "amount": 500})).status_code == 200

    tx = Transaction.signed(buyer, f"{tag}-once", 1, "transfer_tokens",
                            {"to": issuer.address, "amount": 50})
    first, second = _post(tx), _post(tx)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["tx_hash"] == second.json()["tx_hash"]
    assert first.json()["height"] == second.json()["height"]

    balance = client.get(f"/accounts/{buyer.address}", headers=_headers()).json()["balances"]
    assert balance["CarbonCoin"] == 450, f"重复提交不应重复扣款：{balance}"


def test_rebuild_endpoint_works_on_a_live_node():
    resp = client.post("/integrity/rebuild", headers=_headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rebuilt"] is True
    assert body["valid"] is True
    assert body["state_consistent"] is True
