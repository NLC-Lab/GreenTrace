from greentrace.crypto import Identity
from greentrace.models import Transaction
from greentrace.node import Node

import time


def submit(node, identity, request_id, nonce, action, payload):
    return node.append_block([Transaction.signed(identity, request_id, nonce, action, payload)])


def _govern_organization(node, organization_id, members):
    """Create an organization and its members through signed governance transactions.

    Anything that must survive :meth:`Node.rebuild_state` has to be reproducible from
    the chain, so tests that rebuild a node must not seed roles with
    ``contract.grant()`` alone.
    """
    submit(node, node.identity, f"gov-{organization_id}", 1,
           "governance_register_organization",
           {"organization_id": organization_id, "name": organization_id})
    for index, (identity, role) in enumerate(members, start=2):
        submit(node, node.identity, f"gov-{organization_id}-m{index}", index,
               "governance_add_member",
               {"organization_id": organization_id, "address": identity.address, "role": role})


def test_transfer_does_not_reduce_total_credits_and_retirement_finishes():
    node = Node("credits-final", ["n1", "n2", "n3", "n4"])
    owner, buyer, auditor = Identity(), Identity(), Identity()
    for identity, role in ((owner, "enterprise"), (buyer, "buyer"), (auditor, "auditor")):
        node.contract.grant(identity.address, role)
    submit(node, owner, "batch", 1, "register_batch", {"batch_id": "B", "carbon_kg": "0.300"})
    submit(node, owner, "data", 2, "submit_carbon",
           {"batch_id": "B", "version": "v1", "evidence_hash": "x"})
    submit(node, auditor, "audit", 1, "audit_carbon",
           {"batch_id": "B", "status": "approved"})
    submit(node, owner, "move", 3, "transfer_credits",
           {"batch_id": "B", "to": buyer.address, "amount": "0.100"})
    assert str(node.contract.products["B"]["remaining_credits"]) == "0.300"
    submit(node, owner, "retire-owner", 4, "retire_credits",
           {"batch_id": "B", "amount": "0.200"})
    submit(node, buyer, "retire-buyer", 1, "retire_credits",
           {"batch_id": "B", "amount": "0.100"})
    assert node.contract.products["B"]["remaining_credits"] == 0
    assert node.contract.products["B"]["status"] == "retired"


def test_governance_rebuild_works_on_a_live_node_and_drops_local_role_edits():
    node = Node("governance-final", ["n1", "n2", "n3", "n4"])
    member = Identity()
    _govern_organization(node, "org-final", [(member, "buyer")])
    assert node.contract.organizations["org-final"].members[member.address].role == "buyer"

    # No organizations.clear(): a live node must be rebuildable as-is. This is the
    # path behind POST /integrity/rebuild, which used to answer 409 with
    # "organization already exists" as soon as one governance transaction existed.
    assert node.rebuild_state()
    assert "org-final" in node.contract.organizations
    assert node.contract.organizations["org-final"].members[member.address].role == "buyer"
    assert node.contract.roles[member.address] == "buyer"
    assert node.verify_full()

    outsider = Identity()
    node.contract.roles[outsider.address] = "admin"
    assert node.rebuild_state()
    assert outsider.address not in node.contract.roles
    assert node.contract.roles == {node.address: "admin", member.address: "buyer"}


def test_rebuild_replays_governance_from_the_chain_when_state_is_lost(tmp_path):
    node = Node("governance-lost", ["n1", "n2", "n3", "n4"], tmp_path)
    member = Identity()
    _govern_organization(node, "org-lost", [(member, "supplier")])
    (tmp_path / "governance-lost.state.json").unlink()

    reopened = Node("governance-lost", ["n1", "n2", "n3", "n4"], tmp_path)
    assert reopened.contract.organizations == {}
    assert reopened.rebuild_state()
    assert reopened.contract.organizations["org-lost"].members[member.address].role == "supplier"
    assert reopened.contract.roles == {reopened.address: "admin", member.address: "supplier"}


def test_replay_judges_credential_expiry_at_block_time(tmp_path):
    """Replay must use the block timestamp, not wall-clock time.

    A credential that was valid when its transaction was committed must not make the
    chain unrebuildable after it expires.
    """
    node = Node("expiry-final", ["n1", "n2", "n3", "n4"], tmp_path)
    member = Identity()
    submit(node, node.identity, "gov-org", 1, "governance_register_organization",
           {"organization_id": "org-exp", "name": "Org"})
    submit(node, node.identity, "gov-member", 2, "governance_add_member",
           {"organization_id": "org-exp", "address": member.address, "role": "enterprise",
            "expires_at": time.time() + 2})
    submit(node, member, "batch", 1, "register_batch", {"batch_id": "B-exp", "carbon_kg": 1})
    time.sleep(2.1)                      # the credential is now in the past

    assert node.rebuild_state()
    assert node.contract.products["B-exp"]["carbon_kg"] == 1
    assert node.contract.organizations["org-exp"].members[member.address].role == "enterprise"


def test_validator_tickets_need_the_validator_keys():
    node = Node("tickets", ["n1", "n2", "n3", "n4"])
    submit(node, node.identity, "org", 1, "governance_register_organization",
           {"organization_id": "org", "name": "Org"})
    block = node.chain[0]
    good = {validator: node.consensus.vote_digest(block.hash, "commit", validator)
            for validator in node.consensus.validators}
    assert node.consensus.certify(block.hash, good)
    assert not node.consensus.certify(block.hash,
                                      {v: "forged" for v in node.consensus.validators})

    other = Node("tickets", ["n1", "n2", "n3", "n4"])
    foreign = {v: other.consensus.vote_digest(block.hash, "commit", v)
               for v in node.consensus.validators}
    assert not node.consensus.certify(block.hash, foreign)
