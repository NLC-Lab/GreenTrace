from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node


def test_bootstrap_governance_is_signed_and_rebuildable(tmp_path):
    node = Node("governance", ["n1", "n2", "n3", "n4"], tmp_path)
    member = Identity()
    tx = Transaction.signed(
        node.identity, "invite", 1, "governance_register_organization",
        {"organization_id": "org", "name": "Org"},
    )
    node.append_block([tx])
    node.append_block([Transaction.signed(
        node.identity, "member", 2, "governance_add_member",
        {"organization_id": "org", "address": member.address, "role": "buyer"},
    )])
    assert node.verify_full()
    node.contract.organizations.clear()
    assert node.rebuild_state()
    assert node.contract.organizations["org"].members[member.address].role == "buyer"


def test_full_verification_rejects_historical_nonce_reuse():
    node = Node("history", ["n1", "n2", "n3", "n4"])
    identity = Identity()
    node.contract.grant(identity.address, "issuer")
    node.append_block([Transaction.signed(
        identity, "one", 1, "mint_tokens", {"to": identity.address, "amount": 1},
    )])
    second = Transaction.signed(
        identity, "two", 2, "mint_tokens", {"to": identity.address, "amount": 1},
    )
    node.append_block([second])
    node.chain[1].transactions[0].nonce = 1
    node.chain[1].hash = node.chain[1].calculate_hash()
    assert not node.verify_full()
