from concurrent.futures import ThreadPoolExecutor

from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def test_concurrent_same_nonce_commits_once():
    node = Node("concurrent-audit", ["n1", "n2", "n3", "n4"])
    issuer, buyer = Identity(), Identity()
    node.contract.grant(issuer.address, "issuer")
    node.contract.grant(buyer.address, "buyer")
    node.append_block([Transaction.signed(
        issuer, "fund", 1, "mint_tokens",
        {"to": buyer.address, "amount": 100},
    )])
    transactions = [
        Transaction.signed(
            buyer, f"spend-{index}", 1, "transfer_tokens",
            {"to": issuer.address, "amount": 10},
        )
        for index in range(12)
    ]

    def submit(tx):
        try:
            node.append_block([tx])
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(submit, transactions))
    assert sum(accepted) == 1
    assert node.contract.balance(buyer.address) == 90
    assert node.verify_full()


def test_asset_owner_and_chain_id_are_enforced():
    node = Node("ownership-audit", ["n1", "n2", "n3", "n4"])
    owner, attacker, auditor = Identity(), Identity(), Identity()
    for identity, role in ((owner, "enterprise"), (attacker, "enterprise"),
                           (auditor, "auditor")):
        node.contract.grant(identity.address, role)
    node.append_block([Transaction.signed(
        owner, "batch", 1, "register_batch", {"batch_id": "B", "carbon_kg": 100},
    )])
    node.append_block([Transaction.signed(
        owner, "carbon", 2, "submit_carbon",
        {"batch_id": "B", "version": "v1", "evidence_hash": "report"},
    )])
    node.append_block([Transaction.signed(
        auditor, "audit", 1, "audit_carbon",
        {"batch_id": "B", "passed": True},
    )])
    for action, payload in (
        ("transfer_credits", {"batch_id": "B", "amount": 10, "to": attacker.address}),
        ("retire_credits", {"batch_id": "B", "amount": 10}),
    ):
        try:
            node.append_block([Transaction.signed(
                attacker, f"attack-{action}", 1, action, payload,
            )])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{action} moved another owner's credits")

    other_chain_tx = Transaction.signed(
        owner, "wrong-chain", 3, "submit_carbon",
        {"batch_id": "B", "version": "v2", "evidence_hash": "report"},
        chain_id="another-network",
    )
    try:
        node.append_block([other_chain_tx])
    except ValueError as exc:
        assert "chain_id" in str(exc)
    else:
        raise AssertionError("cross-chain transaction was accepted")


def test_full_verification_rejects_tampered_signed_transaction():
    node = Node("tamper-audit", ["n1", "n2", "n3", "n4"])
    issuer = Identity()
    node.contract.grant(issuer.address, "issuer")
    node.append_block([Transaction.signed(
        issuer, "mint", 1, "mint_tokens", {"to": issuer.address, "amount": 1},
    )])
    node.chain[0].transactions[0].payload["amount"] = 999999
    node.chain[0].hash = node.chain[0].calculate_hash()
    assert not node.verify_full()
