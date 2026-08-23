from gltest import get_contract_factory
from gltest.assertions import tx_execution_succeeded


def test_translation_verifier_deploy_topup_request_and_balance_view():
    factory = get_contract_factory("TranslationVerifier")
    contract = factory.deploy()

    tx = contract.top_up(args=[10 ** 18]).transact()
    assert tx_execution_succeeded(tx)

    tx = contract.request_translation(
        args=["r1", "Hello world.", "French", 10 ** 17]
    ).transact()
    assert tx_execution_succeeded(tx)

    owner = contract.get_owner().call()
    balance = contract.my_balance(args=[owner]).call()
    assert int(balance) == 10 ** 18 - 10 ** 17
    assert int(contract.get_total_withdrawn().call()) == 0


def test_translation_verifier_views_after_request():
    factory = get_contract_factory("TranslationVerifier")
    contract = factory.deploy()

    tx = contract.top_up(args=[2 * 10 ** 17]).transact()
    assert tx_execution_succeeded(tx)

    tx = contract.request_translation(
        args=["r2", "Good morning.", "German", 10 ** 17]
    ).transact()
    assert tx_execution_succeeded(tx)

    record = contract.get_request(args=["r2"]).call()
    assert record["target_lang"] == "German"
    assert contract.request_state(args=["r2"]).call() == "open"
