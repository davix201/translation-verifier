import json

CONTRACT = "contracts/TranslationVerifier.py"

REWARD = 10 ** 18
DEPOSIT = 4 * REWARD
REQ = "req-1"

PASS_JSON = json.dumps({"faithful": True, "major_issues": 0, "reason": "meaning preserved"})
FAIL_JSON = json.dumps({"faithful": False, "major_issues": 2, "reason": "omitted second sentence"})
PROMPT_RE = r"translation verifier"


def _addr_str(addr):
    if isinstance(addr, (bytes, bytearray)):
        return "0x" + bytes(addr).hex()
    return str(addr)


def _setup_submitted(direct_vm, contract, requester, translator):
    direct_vm.sender = requester
    contract.top_up(DEPOSIT)
    contract.request_translation(
        REQ,
        "The quick brown fox jumps over the lazy dog.",
        "German",
        REWARD,
    )
    direct_vm.sender = translator
    contract.accept(REQ)
    contract.submit_translation(
        REQ,
        "Der schnelle braune Fuchs springt ueber den faulen Hund.",
    )
    direct_vm.sender = requester


def test_request_and_views_happy(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.top_up(DEPOSIT)
    contract.request_translation(REQ, "Hello world.", "French", REWARD)
    record = contract.get_request(REQ)
    assert record["source_text"] == "Hello world."
    assert record["target_lang"] == "French"
    assert int(record["reward_atto"]) == REWARD
    assert record["status"] == "open"
    assert record["requester"].lower() == _addr_str(direct_alice).lower()
    assert record["translator"] == ""
    assert contract.request_state(REQ) == "open"
    assert int(contract.my_balance(_addr_str(direct_alice))) == DEPOSIT - REWARD


def test_invalid_input_and_duplicate_reverts(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    with direct_vm.expect_revert("insufficient balance"):
        contract.request_translation(REQ, "Text", "German", REWARD)
    contract.top_up(REWARD)
    with direct_vm.expect_revert("[EXPECTED] reward must be positive"):
        contract.request_translation("r0", "Text", "German", 0)
    with direct_vm.expect_revert("source text and target language must not be empty"):
        contract.request_translation("r2", "", "German", REWARD)
    contract.request_translation(REQ, "Text", "German", REWARD)
    with direct_vm.expect_revert("already exists"):
        contract.request_translation(REQ, "Other text", "French", REWARD)
    with direct_vm.expect_revert("unknown request id ghost"):
        contract.request_state("ghost")


def test_accept_locks_translator_and_blocks_self_accept(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.top_up(DEPOSIT)
    contract.request_translation(REQ, "Text", "German", REWARD)

    with direct_vm.expect_revert("[EXPECTED] the requester cannot accept their own request"):
        contract.accept(REQ)

    direct_vm.sender = direct_bob
    contract.accept(REQ)
    assert contract.request_state(REQ) == "accepted"

    with direct_vm.expect_revert("[EXPECTED] request req-1 is not open"):
        contract.accept(REQ)


def test_submit_guards(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.sender = direct_charlie
    with direct_vm.expect_revert("only the locked translator can submit"):
        contract.submit_translation(REQ, "interloper text")
    with direct_vm.expect_revert("unknown request id ghost"):
        contract.request_state("ghost")


def test_submit_before_accept_reverts(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.top_up(DEPOSIT)
    contract.request_translation(REQ, "Text", "German", REWARD)
    with direct_vm.expect_revert("only the locked translator can submit"):
        contract.submit_translation(REQ, "premature translation")
    with direct_vm.expect_revert("has no translation to verify"):
        contract.verify(REQ)


def test_release_flow_faithful_true(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_RE, PASS_JSON)
    result = json.loads(contract.verify(REQ))

    assert result["faithful"] is True
    assert result["status"] == "completed"
    assert result["major_issues"] == 0
    assert contract.request_state(REQ) == "completed"
    assert int(contract.my_balance(_addr_str(direct_bob))) == REWARD
    assert int(contract.my_balance(_addr_str(direct_alice))) == DEPOSIT - REWARD
    record = contract.get_request(REQ)
    assert record["verified"] is True
    assert record["reason"] == "meaning preserved"


def test_refund_flow_faithful_false(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_RE, FAIL_JSON)
    direct_vm.sender = direct_bob
    result = json.loads(contract.verify(REQ))

    assert result["faithful"] is False
    assert result["status"] == "refunded"
    assert int(result["major_issues"]) == 2
    assert contract.request_state(REQ) == "refunded"
    assert int(contract.my_balance(_addr_str(direct_alice))) == DEPOSIT
    assert int(contract.my_balance(_addr_str(direct_bob))) == 0
    record = contract.get_request(REQ)
    assert record["verified"] is True
    assert record["reason"] == "omitted second sentence"


def test_double_verify_reverts(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_RE, PASS_JSON)
    contract.verify(REQ)
    with direct_vm.expect_revert("already verified (appeals are not supported)"):
        contract.verify(REQ)


def test_ledger_topup_withdraw(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.top_up(DEPOSIT)
    assert int(contract.my_balance(_addr_str(direct_alice))) == DEPOSIT
    contract.withdraw()
    assert int(contract.my_balance(_addr_str(direct_alice))) == 0
    assert int(contract.get_total_withdrawn()) == DEPOSIT
    with direct_vm.expect_revert("[EXPECTED] no balance to withdraw"):
        contract.withdraw()


def test_insufficient_funds_request_reverts(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy(CONTRACT)
    direct_vm.sender = direct_alice
    contract.top_up(REWARD - 1)
    with direct_vm.expect_revert("[EXPECTED] insufficient balance: have"):
        contract.request_translation(REQ, "Text", "German", REWARD)
    assert int(contract.my_balance(_addr_str(direct_alice))) == REWARD - 1


def test_llm_garbage_raises_llm_error_keeps_state(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_RE, "cannot comply")
    with direct_vm.expect_revert("[LLM_ERROR]"):
        contract.verify(REQ)

    assert contract.request_state(REQ) == "submitted"
    assert int(contract.my_balance(_addr_str(direct_bob))) == 0
    assert int(contract.my_balance(_addr_str(direct_alice))) == DEPOSIT - REWARD


def test_validator_agrees_then_rotates(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy(CONTRACT)
    _setup_submitted(direct_vm, contract, direct_alice, direct_bob)

    direct_vm.mock_llm(PROMPT_RE, PASS_JSON)
    contract.verify(REQ)
    assert direct_vm.run_validator() is True

    direct_vm.clear_mocks()
    direct_vm.mock_llm(PROMPT_RE, FAIL_JSON)
    assert direct_vm.run_validator() is False
