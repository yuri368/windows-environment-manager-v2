"""Service-to-helper rule proof integration; FileEnvironment only, no UAC/Registry."""
from contextvars import copy_context
import tempfile
import unittest
from unittest.mock import patch
import uuid

from envmanager_v3.native_ipc import consume_review, verify_rule_proofs
from envmanager_v3.service import Service


class SystemProofIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(self.temp.name)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def imported(self, *, mode="-append", raw="A"):
        state = self.service.import_workspace({"text":
            '[Tool.P]\nVALUE={scope="System",mode="' + mode + '",default_value="' + raw + '"}\n'})
        return state["apps"][0]["profiles"][0]["vars"][0]["recordId"]

    def proof(self, receipt):
        holder = {}

        def factory(plans, root, *, job_id):
            from envmanager_v3.system_helper import validate_request
            normalized = validate_request({"protocol": 1, "job_id": job_id, "restore": False, "plans": plans}, allow_type_change=True)
            holder.update(plans=normalized["plans"], context=copy_context(), operation=job_id)
            return object()

        token = "server-consumed-review-token"
        with patch("envmanager_v3.privileges.prepare_system_batch", side_effect=factory):
            self.service._system_session(receipt, token, "a" * 32)
        envelope = holder["context"].run(consume_review, holder["plans"], self.service.planner.engine_hash)
        self.assertEqual(envelope["review_token"], token)
        self.assertEqual(envelope["inputs"]["receipt"], receipt)
        verify_rule_proofs(envelope, {"rules_sha256": self.service.planner.engine_hash},
                           rule_call=lambda package, method, options: self.service.planner.call(method, options))
        with self.assertRaises(Exception):
            holder["context"].run(consume_review, holder["plans"], self.service.planner.engine_hash)
        return envelope

    def execute(self, receipt):
        reviewed = self.service.review(receipt["planId"], {"version": receipt["version"], "digest": receipt["digest"]})
        result = self.service.execute(receipt["planId"], {**reviewed, "idempotencyKey": uuid.uuid4().hex})
        self.assertEqual(result["status"], "committed", result)

    def test_apply_and_contribution_undo_recompute_actual_service_proofs(self):
        record = self.imported()
        self.service.environment.write("System", "VALUE", "X")
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.proof(receipt)
        self.execute(receipt)
        receipt = self.service.plan({"kind": "undo", "recordIds": [record]})
        self.proof(receipt)
        self.assertEqual(receipt["targets"][0]["plan"]["after"]["raw"], "X")

    def test_manual_type_change_and_explicit_delete_use_actual_proofs(self):
        self.service.environment.write("System", "VALUE", "A;;A", type="REG_SZ")
        edit = self.service.open_edit({"scope": "System", "name": "VALUE"})
        self.service.update_edit(edit["sessionId"], {"expectedRevision": 0, "type": "REG_EXPAND_SZ"})
        receipt = self.service.plan({"kind": "manual", "editSessionId": edit["sessionId"]})
        self.proof(receipt)
        self.assertTrue(any(op["action"] == "TYPE_CHANGE" for op in receipt["targets"][0]["plan"]["operations"]))
        self.service.update_edit(edit["sessionId"], {"expectedRevision": 1, "deleteVariable": True})
        self.proof(self.service.plan({"kind": "manual", "editSessionId": edit["sessionId"]}))

    def test_custom_order_recomputes_permutation_without_arbitrary_after(self):
        record = self.imported(mode="append")
        self.service.environment.write("System", "VALUE", "X;;X")
        intent = {"kind": "apply", "recordIds": [record]}
        candidate = self.service.plan(intent)
        order = self.service.open_order({"planId": candidate["planId"]})
        self.service.update_order(order["sessionId"], {"expectedRevision": 0, "order": [r["id"] for r in reversed(order["rows"])]})
        self.proof(self.service.plan({**intent, "orderSessionId": order["sessionId"]}))

    def test_history_restore_has_exact_verified_source(self):
        record = self.imported(mode="replace")
        self.service.environment.write("System", "VALUE", "old")
        receipt = self.service.plan({"kind": "apply", "recordIds": [record]})
        self.execute(receipt)
        history_id = self.service.history({})["items"][0]["id"]
        self.proof(self.service.plan({"kind": "restore_history", "historyIds": [history_id]}))
