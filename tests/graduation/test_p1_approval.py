"""P1: a write that needs approval. Reject: zero side effects. Approve: exactly the pinned call.

Neither shipped plugin writes anything, so the write is a TEST-ONLY plugin defined here
(``ledger``: ``ledger_append``, ``effect: write``, ``approval: pinned``) -- it ships in neither
package. Its side effect is a list the test owns, so "zero side effects" is a plain
``== []``, and what it was called with is recorded as a deep copy, so "exact pinned arguments, no
post-approval mutation" is a plain ``==``.

Both ways a call reaches an approval are covered, because they resume differently:

* **from code** (``api.tools.call`` by another plugin): queued with the caller's stamp, run
  once by the harness on approval, the caller hears the outcome on ``on_approved_call``;
* **from the model** (``chat`` and ``chat_stream``): the turn halts on the approval, and a
  resumed run executes exactly the pinned call.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from iris_harness.sdk import PluginAPI
from iris_harness.sdk.approvals import ApprovalAlreadyAnsweredError, ApprovalQueue, ApprovalStore
from iris_harness.testing import Harness, check_conformance, harness, plugin

from .support import answer_rule, call_tool_rule, raw_rows, script

ARGS = {"entry": "rent", "amount": 900}
LEDGER_MANIFEST = {
    "name": "ledger",
    "provides": ["tool"],
    "tools": {"ledger_append": {"effect": "write", "approval": "pinned"}},
}


class Ledger:
    """The write's world: what was appended, and every argument dict the tool was handed."""

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []

    def plugin(self):
        def append(args: dict[str, Any]) -> str:
            self.received.append(copy.deepcopy(args))
            self.entries.append(copy.deepcopy(args))
            return f"appended {args['entry']}"

        def setup(api: PluginAPI) -> None:
            api.register_tool(
                "ledger_append", 'Append an entry. Args: {"entry": str, "amount": int}.', append
            )

        return plugin(setup, manifest=LEDGER_MANIFEST)


class Bookkeeper:
    """A second plugin that may call ``ledger_append`` from code and hears how it ended."""

    def __init__(self) -> None:
        self.api: PluginAPI | None = None
        self.outcomes: list[tuple[str, str]] = []

    def plugin(self):
        def setup(api: PluginAPI) -> None:
            self.api = api
            api.on_approved_call(lambda p: self.outcomes.append((p.tool, p.status)))

        manifest = {"name": "bookkeeper", "uses": {"tools": ["ledger_append"]}}
        return plugin(setup, manifest=manifest)


def pending() -> list[Any]:
    return ApprovalQueue(store=ApprovalStore()).list_pending()


def rows(h: Harness) -> list[dict]:
    return raw_rows(h, tool="ledger_append")


def digests(h: Harness, decision: str | None = None) -> set[str]:
    return {
        r["payload"]["args_digest"]
        for r in rows(h)
        if r["hook"] == "pre_tool_use"
        and "args_digest" in r["payload"]
        and (decision is None or r["decision"] == decision)
    }


# -- from code --------------------------------------------------------------------------------
def test_from_code_a_write_is_held_and_nothing_is_written_until_the_owner_answers() -> None:
    ledger, book = Ledger(), Bookkeeper()
    with harness(plugins=[ledger.plugin(), book.plugin()]):
        result = book.api.tools.call("ledger_append", dict(ARGS))
        assert result.held and not result.ok and result.approval_id
        assert ledger.entries == []  # held, not run
        [row] = pending()
        assert row.approval_id == result.approval_id and row.caller == "plugin:bookkeeper"
        assert [(i.tool, json.loads(i.args_json)) for i in row.items] == [("ledger_append", ARGS)]
        assert row.card and "act on your behalf" in row.card.title  # words, not ids
        assert ledger.entries == []


def test_from_code_reject_means_zero_side_effects_and_the_caller_is_told() -> None:
    ledger, book = Ledger(), Bookkeeper()
    with harness(plugins=[ledger.plugin(), book.plugin()]) as h:
        held = book.api.tools.call("ledger_append", dict(ARGS))
        told = h.respond_to_approval(held.approval_id, approve=False)
        assert told == "The owner rejected it; nothing was run."
        assert ledger.entries == [] and ledger.received == []  # the tool function never ran
        assert book.outcomes == [("ledger_append", "rejected")]
        assert not [r for r in rows(h) if r["hook"] == "post_tool_use"]  # no "used" row either
        decisions = [r["decision"] for r in raw_rows(h, hook="approval_queue")]
        assert "rejected" in decisions and "call_rejected" in decisions
        assert "call_ran" not in decisions and "claimed" not in decisions
        # a rejected approval cannot be revived by answering again
        with pytest.raises(ApprovalAlreadyAnsweredError):
            h.respond_to_approval(held.approval_id, approve=True)
        assert ledger.entries == []


def test_from_code_approve_runs_exactly_the_pinned_arguments_exactly_once() -> None:
    ledger, book = Ledger(), Bookkeeper()
    with harness(plugins=[ledger.plugin(), book.plugin()]) as h:
        args = dict(ARGS)
        held = book.api.tools.call("ledger_append", args)
        # the caller keeps hold of its dict and rewrites it AFTER the call was queued
        args["entry"], args["amount"] = "evil", 1
        h.respond_to_approval(held.approval_id, approve=True)
        assert ledger.received == [ARGS]  # what the tool was handed: the pinned call
        assert ledger.entries == [ARGS]
        assert book.outcomes == [("ledger_append", "ran")]
        # the digest the run was governed under is the digest it was queued under
        queued, ran = digests(h, "require_approval"), digests(h, "allow")
        assert len(queued) == 1 and ran == queued, (queued, ran)
        assert [r["decision"] for r in raw_rows(h, hook="approval_queue")].count("call_ran") == 1
        # answering again must not run it again
        with pytest.raises(ApprovalAlreadyAnsweredError):
            h.respond_to_approval(held.approval_id, approve=True)
        assert ledger.entries == [ARGS]


def test_the_digest_binds_the_arguments_a_different_call_gets_a_different_digest() -> None:
    ledger, book = Ledger(), Bookkeeper()
    with harness(plugins=[ledger.plugin(), book.plugin()]) as h:
        book.api.tools.call("ledger_append", dict(ARGS))
        book.api.tools.call("ledger_append", {"entry": "rent", "amount": 901})
        assert len(digests(h, "require_approval")) == 2
        assert ledger.entries == []


def test_each_queued_call_is_its_own_approval_and_runs_only_its_own_arguments() -> None:
    ledger, book = Ledger(), Bookkeeper()
    with harness(plugins=[ledger.plugin(), book.plugin()]) as h:
        a = book.api.tools.call("ledger_append", {"entry": "rent", "amount": 900})
        b = book.api.tools.call("ledger_append", {"entry": "power", "amount": 80})
        h.respond_to_approval(b.approval_id, approve=True)
        h.respond_to_approval(a.approval_id, approve=False)
        assert ledger.entries == [{"entry": "power", "amount": 80}]


# -- from the model, on both turn entry points -----------------------------------------------------
@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_from_the_model_reject_means_zero_side_effects(entry: str) -> None:
    ledger = Ledger()
    rules = script(
        answer_rule("appended", "Recorded."), call_tool_rule("Record rent", "ledger_append", ARGS)
    )
    with harness(plugins=[ledger.plugin()], fake_model=rules) as h:
        halted = getattr(h, entry)("Record rent")
        assert halted.answered and "needs your approval" in halted.text
        assert ledger.entries == []
        [row] = pending()
        assert [json.loads(i.args_json) for i in row.items] == [ARGS]
        h.respond_to_approval(row.approval_id, approve=False)
        assert ledger.entries == [] and ledger.received == []
        assert not [r for r in rows(h) if r["hook"] == "post_tool_use"]
        assert h.audit_gaps() == []


@pytest.mark.parametrize("entry", ["chat", "chat_stream"])
def test_from_the_model_approve_runs_exactly_the_pinned_call_and_resumes_the_turn(
    entry: str,
) -> None:
    ledger = Ledger()
    rules = script(
        answer_rule("appended", "Recorded."), call_tool_rule("Record rent", "ledger_append", ARGS)
    )
    with harness(plugins=[ledger.plugin()], fake_model=rules) as h:
        getattr(h, entry)("Record rent")
        [row] = pending()
        resumed = h.respond_to_approval(row.approval_id, approve=True)
        assert resumed == "Recorded."  # the halted turn picked up and finished
        assert ledger.received == [ARGS] and ledger.entries == [ARGS]
        queued, ran = digests(h, "require_approval"), digests(h, "allow")
        assert len(queued) == 1 and ran == queued, (queued, ran)
        post = [r for r in rows(h) if r["hook"] == "post_tool_use"]
        assert {r["payload"]["caller"] for r in post} == {"model:system"}
        assert len({r["id"] for r in post if r["plugin"] == "output_classifier"}) == 1  # one run
        assert h.audit_gaps() == []


# -- the Governance Conformance Suite on the same plugin --------------------------------------------
def test_the_conformance_suite_agrees_it_is_held_rejected_approved_pinned_and_audited() -> None:
    ledger = Ledger()
    violations = check_conformance(ledger.plugin(), tools={"ledger_append": dict(ARGS)})
    assert violations == []
    # the suite ran the approving harness (once) and the rejecting harness (never)
    assert ledger.entries == [ARGS]


def test_the_chat_halt_message_does_not_claim_a_write_deletes_data() -> None:
    ledger = Ledger()
    rules = script(
        answer_rule("appended", "Recorded."), call_tool_rule("Record rent", "ledger_append", ARGS)
    )
    with harness(plugins=[ledger.plugin()], fake_model=rules) as h:
        halted = h.chat("Record rent")
        assert "needs your approval" in halted.text
        assert "delete" not in halted.text.lower()
