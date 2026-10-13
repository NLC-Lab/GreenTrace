"""GreenTrace 绿色凭证业务合约与角色权限。"""
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from .models import Member, Organization
from .canonical import canonical_hash


ROLES = {
    "enterprise": {"register_batch", "submit_carbon", "transfer_credits", "retire_credits",
                   "transfer_tokens", "supplier_reject_order", "extend_order", "quality_reject"},
    "supplier": {"register_batch", "submit_carbon", "transfer_credits", "retire_credits",
                 "transfer_tokens",                  "acknowledge_order", "deliver_order", "partial_delivery",
                 "supplier_reject_order", "extend_order"},
    "producer": {"create_product", "register_batch", "submit_carbon", "transfer_credits",
                 "retire_credits", "transfer_tokens"},
    "auditor": {"audit_carbon", "audit_product", "rework_carbon"},
    "buyer": {"transfer_product", "transfer_credits", "retire_credits", "transfer_tokens",
              "create_order", "accept_order", "partial_accept", "reject_order",
              "dispute_order", "cancel_order", "settle_order", "quality_reject"},
    "manufacturer": {"transfer_tokens", "acknowledge_order", "deliver_order",
                      "confirm_order", "ship_order", "partial_delivery", "extend_order",
                      "supplier_reject_order"},
    "brand": {"transfer_tokens", "create_order", "accept_order", "partial_accept",
              "reject_order", "dispute_order", "cancel_order", "settle_order"},
    "regulator": {"freeze_credits", "unfreeze_credits", "revoke_credits", "mint_tokens", "freeze_account",
                  "unfreeze_account", "resolve_dispute", "adjudicate_order", "refund_order", "release_escrow"},
    "admin": {"register_batch", "submit_carbon", "audit_carbon", "transfer_credits",
              "governance_register_organization", "governance_add_member", "governance_revoke_member",
              "governance_deactivate_organization",
              "freeze_credits", "unfreeze_credits", "revoke_credits", "retire_credits",
              "create_product", "audit_product", "mint_tokens", "freeze_account",
              "unfreeze_account", "transfer_tokens", "create_order", "acknowledge_order",
              "deliver_order", "partial_delivery", "accept_order", "partial_accept",
              "reject_order", "cancel_order", "dispute_order", "resolve_dispute",
              "refund_order", "release_escrow", "settle_order", "supplier_reject_order",
              "extend_order", "quality_reject", "adjudicate_order"},
    "issuer": {"mint_tokens", "transfer_tokens"},
}
# Governance is deliberately explicit: invitation decisions and member
# deactivation are transactions, never implicit server-side mutations.
ROLES["admin"].update({"governance_invite_member", "governance_member_decision",
                       "governance_deactivate_member"})


class ContractError(ValueError):
    pass


class GreenContract:
    def __init__(self):
        self.roles: dict[str, str] = {}
        self.products: dict[str, dict[str, Any]] = {}
        self.organizations: dict[str, Organization] = {}
        self.balances: dict[str, dict[str, Any]] = {}
        self.frozen_accounts: set[str] = set()
        self.ledger: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.supply_chain: list[dict[str, str]] = []
        self._rewards: set[str] = set()

    def grant(self, address: str, role: str) -> None:
        if role not in ROLES:
            raise ContractError("unknown role")
        self.roles[address] = role

    def register_organization(self, organization_id: str, name: str) -> Organization:
        if not organization_id or organization_id in self.organizations:
            raise ContractError("organization already exists or missing id")
        organization = Organization(organization_id=organization_id, name=name)
        self.organizations[organization_id] = organization
        return organization

    def add_member(self, organization_id: str, address: str, role: str,
                   expires_at: float | None = None) -> Member:
        if organization_id not in self.organizations:
            raise ContractError("organization not found")
        if role not in ROLES:
            raise ContractError("unknown role")
        member = Member(address=address, organization_id=organization_id, role=role,
                        expires_at=expires_at)
        self.organizations[organization_id].members[address] = member
        self.roles[address] = role
        return member

    def invite_member(self, organization_id: str, address: str, role: str,
                      expires_at: float | None = None) -> Member:
        member = self.add_member(organization_id, address, role, expires_at)
        member.invitation_status = "invited"
        member.active = False
        self.roles.pop(address, None)
        return member

    def member_decision(self, address: str, accept: bool, now: float | None = None) -> Member:
        for organization in self.organizations.values():
            member = organization.members.get(address)
            if member:
                if member.invitation_status != "invited":
                    raise ContractError("invitation is not pending")
                if member.expires_at is not None and member.expires_at <= (time.time() if now is None else now):
                    member.invitation_status = "rejected"
                    raise ContractError("invitation expired")
                member.invitation_status = "accepted" if accept else "rejected"
                member.active = accept
                if accept:
                    self.roles[address] = member.role
                else:
                    self.roles.pop(address, None)
                return member
        raise ContractError("member not found")

    def deactivate_member(self, address: str) -> None:
        for organization in self.organizations.values():
            if address in organization.members:
                organization.members[address].active = False
                organization.members[address].invitation_status = "revoked"
        self.roles.pop(address, None)

    def deactivate_organization(self, organization_id: str) -> None:
        organization = self.organizations.get(organization_id)
        if organization is None:
            raise ContractError("organization not found")
        organization.active = False

    def authorize_member(self, address: str, action: str, now: float | None = None) -> None:
        role = self.roles.get(address)
        organization = next((o for o in self.organizations.values()
                             if address in o.members), None)
        member = organization.members.get(address) if organization else None
        if member and (not member.active or
                       (member.expires_at is not None
                        and member.expires_at <= (time.time() if now is None else now))):
            raise ContractError("credential inactive or expired")
        if organization and not organization.active:
            raise ContractError("organization inactive")
        if role is None or action not in ROLES[role]:
            raise ContractError(f"permission denied: {action}")

    def balance(self, address: str, token: str = "CarbonCoin") -> int:
        return int(self.balances.get(address, {}).get(token, 0))

    @staticmethod
    def _amount(value: Any, name: str = "amount") -> int:
        try:
            invalid = isinstance(value, bool) or int(value) != float(value) or int(value) < 0
        except (TypeError, ValueError, OverflowError):
            invalid = True
        if invalid:
            raise ContractError(f"{name} must be a non-negative integer")
        return int(value)

    @staticmethod
    def _carbon_amount(value: Any, name: str = "carbon_kg") -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ContractError(f"{name} must be a non-negative decimal") from None
        if not amount.is_finite() or amount < 0:
            raise ContractError(f"{name} must be a non-negative decimal")
        return amount.quantize(Decimal("0.001"))

    def _credit(self, address: str, token: str, amount: int) -> None:
        self.balances.setdefault(address, {})[token] = self.balance(address, token) + amount

    def _debit(self, address: str, token: str, amount: int) -> None:
        if address in self.frozen_accounts:
            raise ContractError("frozen account cannot transfer")
        if self.balance(address, token) < amount:
            raise ContractError("insufficient balance")
        self.balances[address][token] -= amount

    def _reward(self, key: str, address: str, points: int, reason: str) -> None:
        if key in self._rewards:
            return
        self._rewards.add(key)
        self._credit(address, "GreenPoint", points)
        self.ledger.append({"type": "reward", "token": "GreenPoint", "to": address,
                            "amount": points, "reason": reason, "key": key})

    def apply(self, sender: str, action: str, payload: dict[str, Any],
              now: float | None = None) -> dict[str, Any]:
        # ``now`` is the block timestamp during replay and wall-clock time for live
        # submissions.  Replay must use the block time, otherwise a credential that
        # expired after its transaction was committed would make the chain
        # unrebuildable ("credential inactive or expired").
        current = time.time() if now is None else now
        self.authorize_member(sender, action, current)
        if action == "governance_register_organization":
            return self.register_organization(str(payload["organization_id"]), str(payload["name"])).model_dump()
        if action == "governance_add_member":
            return self.add_member(str(payload["organization_id"]), str(payload["address"]),
                                   str(payload["role"]), payload.get("expires_at")).model_dump()
        if action == "governance_invite_member":
            return self.invite_member(str(payload["organization_id"]), str(payload["address"]),
                                      str(payload["role"]), payload.get("expires_at")).model_dump()
        if action == "governance_member_decision":
            return self.member_decision(str(payload["address"]), bool(payload.get("accept")),
                                        current).model_dump()
        if action == "governance_revoke_member":
            self.deactivate_member(str(payload["address"]))
            return {"address": str(payload["address"]), "revoked": True}
        if action == "governance_deactivate_member":
            self.deactivate_member(str(payload["address"]))
            return {"address": str(payload["address"]), "revoked": True}
        if action == "governance_deactivate_organization":
            self.deactivate_organization(str(payload["organization_id"]))
            return {"organization_id": str(payload["organization_id"]), "active": False}
        if action in {"mint", "mint_tokens"}:
            token = str(payload.get("token", "CarbonCoin"))
            amount = self._amount(payload.get("amount", 0))
            to = str(payload.get("to") or sender)
            if not token or amount <= 0:
                raise ContractError("mint amount must be positive")
            self._credit(to, token, amount)
            self.ledger.append({"type": "mint", "token": token, "to": to, "amount": amount,
                                "request": payload.get("request_id")})
            return {"to": to, "token": token, "amount": amount, "balance": self.balance(to, token)}
        if action in {"transfer_tokens", "transfer_token"}:
            token = str(payload.get("token", "CarbonCoin"))
            amount = self._amount(payload.get("amount", 0))
            to = str(payload.get("to", ""))
            if not to or amount <= 0:
                raise ContractError("transfer requires a positive amount and recipient")
            if sender in self.frozen_accounts or to in self.frozen_accounts:
                raise ContractError("frozen account cannot transfer")
            self._debit(sender, token, amount)
            self._credit(to, token, amount)
            self.ledger.append({"type": "transfer", "token": token, "from": sender, "to": to,
                                "amount": amount})
            return {"from": sender, "to": to, "token": token, "amount": amount}
        if action in {"freeze_account", "unfreeze_account"}:
            address = str(payload.get("address") or payload.get("account") or "")
            if not address:
                raise ContractError("account address required")
            if action == "freeze_account":
                self.frozen_accounts.add(address)
            else:
                self.frozen_accounts.discard(address)
            return {"address": address, "frozen": address in self.frozen_accounts}
        if action in {"create_order", "acknowledge_order", "confirm_order",
                      "deliver_order", "ship_order", "partial_delivery",
                      "accept_order", "partial_accept", "settle_order",
                      "cancel_order", "reject_order", "dispute_order",
                      "resolve_dispute", "refund_order", "release_escrow",
                      "supplier_reject_order", "extend_order", "quality_reject",
                      "adjudicate_order"}:
            return self._apply_order(sender, action, payload, current)
        product_id = payload.get("product_id")
        batch_id = payload.get("batch_id") or product_id
        if action in {"register_batch", "create_product"}:
            if not batch_id or batch_id in self.products:
                raise ContractError("batch already exists or missing batch_id")
            carbon_amount = self._carbon_amount(payload.get("carbon_kg", 0))
            self.products[batch_id] = {
                **payload, "batch_id": batch_id, "product_id": batch_id,
                "owner": sender, "carbon_kg": carbon_amount, "remaining_credits": carbon_amount,
                "holdings": {sender: carbon_amount},
                "audited": False, "carbon_status": "submitted", "frozen": False, "revoked": False,
                "versions": set(),
                "status": "active",
            }
            return self.products[batch_id]
        if action in {"submit_carbon", "audit_carbon"}:
            if batch_id not in self.products:
                raise ContractError("batch not found")
            batch = self.products[batch_id]
            if action == "submit_carbon":
                if batch["owner"] != sender:
                    raise ContractError("only batch owner may submit carbon data")
                version = str(payload.get("version", ""))
                if not version or version in batch["versions"]:
                    raise ContractError("carbon data version already submitted")
                if not payload.get("evidence_hash"):
                    raise ContractError("evidence_hash is required")
                evidence = payload.get("evidence")
                if evidence is not None and payload["evidence_hash"] != canonical_hash(evidence):
                    raise ContractError("evidence hash mismatch")
                if batch.get("carbon_status") not in {None, "submitted", "rework_required", "rejected"}:
                    raise ContractError("carbon data cannot be replaced after approval")
                batch["versions"].add(version)
                batch["carbon_data"] = {k: v for k, v in payload.items() if k != "batch_id"}
                batch["evidence_hash"] = payload.get("evidence_hash")
                batch["carbon_version"] = version
                batch["carbon_status"] = "submitted"
            else:
                if batch["owner"] == sender:
                    raise ContractError("auditor cannot audit own submission")
                if batch.get("carbon_status") != "submitted":
                    raise ContractError("carbon data must be submitted before review")
                status = str(payload.get("status", "approved" if payload.get("passed", False)
                                   else "rework_required"))
                if status not in {"approved", "rework_required", "rejected"}:
                    raise ContractError("invalid carbon review status")
                batch["carbon_status"] = status
                batch["audited"] = status == "approved"
                batch["auditor"] = sender
                batch["review_reason"] = payload.get("reason", "")
                if batch["audited"]:
                    self._reward(f"audit:{batch_id}", sender, 10, "audit approved")
                    if batch["carbon_kg"] <= Decimal("100"):
                        self._reward(f"low-carbon:{batch_id}", batch["owner"], 5,
                                     "low-carbon batch")
            return batch
        if action in {"audit_product"}:
            if product_id not in self.products:
                raise ContractError("product not found")
            if self.products[product_id]["owner"] == sender:
                raise ContractError("auditor cannot audit own submission")
            self.products[product_id]["audited"] = bool(payload.get("passed", False))
            self.products[product_id]["auditor"] = sender
            if self.products[product_id]["audited"]:
                self._reward(f"audit:{product_id}", sender, 10, "audit approved")
        elif action in {"transfer_product", "transfer_credits"}:
            if batch_id not in self.products:
                raise ContractError("batch not found")
            batch = self.products[batch_id]
            if batch["revoked"] or batch["frozen"] or not batch["audited"]:
                raise ContractError("credits must be audited, unfrozen and not revoked")
            transfer_amount = self._carbon_amount(payload.get("amount", batch.get("remaining_credits", 0)), "amount")
            to = str(payload.get("to", ""))
            sender_holding = batch["holdings"].get(sender, Decimal("0"))
            if sender_holding <= 0:
                raise ContractError("sender does not own credits; transfer exceeds available holding")
            if not to or transfer_amount <= 0 or transfer_amount > sender_holding:
                raise ContractError("credit transfer exceeds remaining balance")
            batch["holdings"][sender] -= transfer_amount
            batch["holdings"][to] = batch["holdings"].get(to, Decimal("0")) + transfer_amount
            if batch["holdings"][sender] == 0:
                batch["holdings"].pop(sender)
            batch["owner"] = next(iter(batch["holdings"])) if len(batch["holdings"]) == 1 else sender
            batch["transfers"] = batch.get("transfers", []) + [{"from": sender, "to": to, "amount": transfer_amount}]
        elif action == "retire_credits":
            if batch_id not in self.products:
                raise ContractError("batch not found")
            batch = self.products[batch_id]
            if batch["revoked"] or batch["frozen"] or not batch["audited"]:
                raise ContractError("credits must be audited, unfrozen and not revoked")
            retirement_amount = self._carbon_amount(payload.get("amount", batch.get("remaining_credits", 0)), "amount")
            sender_holding = batch["holdings"].get(sender, Decimal("0"))
            if sender_holding <= 0:
                raise ContractError("sender does not own credits; retirement exceeds available holding")
            if retirement_amount <= 0 or retirement_amount > sender_holding:
                raise ContractError("retirement exceeds remaining balance")
            batch["remaining_credits"] -= retirement_amount
            batch["holdings"][sender] -= retirement_amount
            if batch["holdings"][sender] == 0:
                batch["holdings"].pop(sender)
            batch["retired_credits"] = Decimal(str(batch.get("retired_credits", 0))) + retirement_amount
            batch["retirements"] = batch.get("retirements", []) + [{
                "owner": sender, "amount": retirement_amount, "reason": payload.get("reason", "")
            }]
            if batch["remaining_credits"] <= Decimal("0.000"):
                batch["status"] = "retired"
        elif action in {"freeze_credits", "unfreeze_credits"}:
            if batch_id not in self.products:
                raise ContractError("batch not found")
            self.products[batch_id]["frozen"] = action == "freeze_credits"
        elif action == "revoke_credits":
            if batch_id not in self.products:
                raise ContractError("batch not found")
            self.products[batch_id]["revoked"] = True
        return self.products.get(str(batch_id or product_id), {})

    def _apply_order(self, sender: str, action: str, payload: dict[str, Any],
                     now: float | None = None) -> dict[str, Any]:
        action = {"confirm_order": "acknowledge_order", "ship_order": "deliver_order"}.get(
            action, action)
        action = {"supplier_reject_order": "supplier_reject",
                  "quality_reject": "reject_order",
                  "extend_order": "delay_order",
                  "adjudicate_order": "resolve_dispute"}.get(action, action)
        order_id = str(payload.get("order_id", ""))
        if action == "create_order":
            if not order_id or order_id in self.orders:
                raise ContractError("order already exists or missing order_id")
            supplier, manufacturer = str(payload.get("supplier", "")), str(payload.get("manufacturer", ""))
            buyer = str(payload.get("buyer") or sender)
            if buyer != sender:
                raise ContractError("buyer must be the signed transaction sender")
            if not supplier or not manufacturer or not buyer:
                raise ContractError("supplier, manufacturer and buyer are required")
            amount = self._amount(payload.get("amount", 0))
            if amount <= 0:
                raise ContractError("order amount must be positive")
            self.orders[order_id] = {**payload, "order_id": order_id, "supplier": supplier,
                                     "manufacturer": manufacturer, "buyer": buyer,
                                     "created_by": sender, "status": "created",
                                     "settled": False, "escrow_locked": False,
                                     "escrow_released": 0, "accepted_amount": 0,
                                     "delivered_amount": 0, "disputed": False,
                                     "extensions": [], "events": []}
            if payload.get("confirmation_deadline") is not None:
                self.orders[order_id]["confirmation_deadline"] = payload["confirmation_deadline"]
            token = str(payload.get("token", "CarbonCoin"))
            self._debit(buyer, token, amount)
            self._credit(self._escrow_account(order_id), token, amount)
            self.orders[order_id]["escrow_locked"] = True
            self.ledger.append({"type": "escrow_lock", "order_id": order_id, "token": token,
                                "from": buyer, "to": self._escrow_account(order_id), "amount": amount})
            self.supply_chain.append({"supplier": supplier, "manufacturer": manufacturer,
                                      "buyer": buyer, "order_id": order_id})
            return self.orders[order_id]
        if order_id not in self.orders:
            raise ContractError("order not found")
        order = self.orders[order_id]
        if action == "acknowledge_order":
            deadline = payload.get("now", time.time() if now is None else now)
            timeout = order.get("confirmation_deadline")
            if timeout is not None and float(deadline) > float(timeout):
                raise ContractError("supplier confirmation timed out")
            expected = ({"created"}, "supplier", "acknowledged")
        elif action == "supplier_reject":
            expected = ({"created"}, "supplier", "supplier_rejected")
        elif action == "delay_order":
            expected = ({"created", "acknowledged", "in_transit", "partial_delivery"},
                        "supplier", "delayed")
        elif action in {"deliver_order", "partial_delivery"}:
            expected = ({"acknowledged", "in_transit", "partial_delivery", "delayed"}, "supplier",
                        "delivered" if action == "deliver_order" else "partial_delivery")
        elif action in {"accept_order", "partial_accept"}:
            expected = ({"delivered", "partial_delivery", "rejected"}, "buyer",
                        "accepted" if action == "accept_order" else "partial_accept")
        elif action == "settle_order":
            expected = ({"accepted", "partial_accept"}, "buyer", "settled")
        elif action == "cancel_order":
            expected = ({"created", "acknowledged"}, "buyer", "cancel")
        elif action == "reject_order":
            expected = ({"delivered", "partial_delivery"}, "buyer", "rejected")
        elif action == "dispute_order":
            expected = ({"delivered", "partial_delivery", "rejected", "partial_accept"}, "buyer", "dispute")
        elif action == "resolve_dispute":
            expected = ({"dispute"}, "regulator", "resolved")
        elif action in {"refund_order", "release_escrow"}:
            expected = ({"cancel", "rejected", "resolved"}, "regulator", "refunded")
        else:
            raise ContractError(f"unsupported order action: {action}")
        valid_states, party, next_status = expected
        if order["status"] not in valid_states:
            if action == "settle_order" and order["settled"]:
                raise ContractError("order already settled")
            raise ContractError(f"invalid order state: {order['status']}")
        allowed_party = (order[party], order["manufacturer"]) if action == "deliver_order" else (
            (order.get(party),) if party in order else ())
        if action in {"resolve_dispute", "refund_order", "release_escrow"}:
            if self.roles.get(sender) not in {"regulator", "admin"}:
                raise ContractError("only regulator or admin may resolve escrow")
        elif sender not in allowed_party:
            raise ContractError(f"only {party} may {action}")
        reason = str(payload.get("reason", ""))
        if action in {"cancel_order", "reject_order", "supplier_reject", "dispute_order",
                      "resolve_dispute", "refund_order", "release_escrow", "delay_order"} and not reason:
            raise ContractError("reason is required")
        if action == "dispute_order" and not payload.get("evidence_hash"):
            raise ContractError("evidence_hash is required for dispute")
        order["reason"] = reason or order.get("reason", "")
        order["responsible_party"] = payload.get("responsible_party", sender)
        if payload.get("evidence_hash"):
            order["evidence_hash"] = payload["evidence_hash"]
        if payload.get("deadline"):
            order["deadline"] = payload["deadline"]
        if payload.get("extend_deadline"):
            if float(payload["extend_deadline"]) <= float(order.get("deadline", 0) or 0):
                raise ContractError("extension must move deadline forward")
            order.setdefault("extensions", []).append({"from": order.get("deadline"),
                                                        "to": payload["extend_deadline"],
                                                        "reason": reason})
            order["deadline"] = payload["extend_deadline"]
        order["status"] = next_status
        order.setdefault("events", []).append({"action": action, "actor": sender, "reason": reason,
                                               "evidence_hash": payload.get("evidence_hash")})
        if action in {"deliver_order", "partial_delivery"}:
            delivered = self._amount(payload.get("delivered_amount",
                                                   order["amount"] if action == "deliver_order"
                                                   else payload.get("amount", 0)), "delivered_amount")
            if delivered <= 0 or order["delivered_amount"] + delivered > order["amount"]:
                raise ContractError("delivered amount exceeds order")
            order["delivered_amount"] += delivered
            if order["delivered_amount"] == order["amount"]:
                order["status"] = "delivered"
        if action in {"accept_order", "partial_accept"}:
            accepted = self._amount(payload.get("accepted_amount",
                                                  order["amount"] if action == "accept_order"
                                                  else payload.get("amount", 0)), "accepted_amount")
            if accepted <= 0 or accepted > order["delivered_amount"] - order["accepted_amount"]:
                raise ContractError("accepted amount exceeds delivered amount")
            order["accepted_amount"] += accepted
            self._settle_order(order, accepted)
            if order["accepted_amount"] == order["amount"]:
                order["status"] = "settled"
                order["settled"] = True
        if action in {"cancel_order", "reject_order", "supplier_reject"}:
            self._refund_order(order, order["amount"] - order["released_amount"]
                               if "released_amount" in order else order["amount"])
        if action == "resolve_dispute":
            decision = str(payload.get("decision", ""))
            if decision not in {"refund", "accept", "partial_refund"}:
                raise ContractError("decision must be refund, accept or partial_refund")
            order["decision"] = decision
            if decision == "refund":
                self._refund_order(order, order["amount"] - order["accepted_amount"])
            elif decision == "partial_refund":
                refund = self._amount(payload.get("refund_amount", 0), "refund_amount")
                self._refund_order(order, refund)
            else:
                self._settle_order(order, order["amount"] - order["accepted_amount"])
        if action == "refund_order":
            self._refund_order(order, self._escrow_balance(order))
        if action == "release_escrow":
            self._settle_order(order, self._escrow_balance(order))
        if action == "deliver_order" and bool(payload.get("on_time", True)):
            self._reward(f"delivery:{order_id}", order["supplier"], 5, "on-time delivery")
        return order

    @staticmethod
    def _escrow_account(order_id: str) -> str:
        return f"escrow:{order_id}"

    def _escrow_balance(self, order: dict[str, Any]) -> int:
        return self.balance(self._escrow_account(order["order_id"]), str(order.get("token", "CarbonCoin")))

    def _settle_order(self, order: dict[str, Any], amount: int | None = None) -> None:
        amount = self._amount(order["amount"] if amount is None else amount)
        if amount == 0:
            return
        token = str(order.get("token", "CarbonCoin"))
        if self._escrow_balance(order) < amount:
            raise ContractError("insufficient escrow")
        self._debit(self._escrow_account(order["order_id"]), token, amount)
        self._credit(order["supplier"], token, amount)
        order["released_amount"] = order.get("released_amount", 0) + amount
        self.ledger.append({"type": "order_settlement", "order_id": order["order_id"],
                            "token": token, "from": self._escrow_account(order["order_id"]),
                            "to": order["supplier"],
                            "buyer": order["buyer"], "supplier": order["supplier"],
                            "amount": amount})

    def _refund_order(self, order: dict[str, Any], amount: int) -> None:
        amount = min(self._amount(amount), self._escrow_balance(order))
        if amount == 0:
            return
        token = str(order.get("token", "CarbonCoin"))
        self._debit(self._escrow_account(order["order_id"]), token, amount)
        self._credit(order["buyer"], token, amount)
        order["released_amount"] = order.get("released_amount", 0) + amount
        order["refunded_amount"] = order.get("refunded_amount", 0) + amount
        self.ledger.append({"type": "order_refund", "order_id": order["order_id"], "token": token,
                            "from": self._escrow_account(order["order_id"]), "to": order["buyer"],
                            "amount": amount})
