# TranslationVerifier

AI-arbitrated translation marketplace: requesters escrow rewards from an internal ledger, a different translator submits the translation, and an LLM verification — confirmed by an independent validator evaluation — releases the reward or refunds the requester.

## How it works

1. `top_up(amount_atto)` credits the caller's internal ledger balance; `withdraw()` zeroes it and tracks `total_withdrawn`.
2. `request_translation(req_id, source_text, target_lang, reward_atto)` immediately moves `reward_atto` from the requester's balance into the request escrow (status `open`).
3. `accept(req_id)` locks exactly one translator, who must not be the requester (status `accepted`); `submit_translation(req_id, translated_text)` is translator-only (status `submitted`).
4. `verify(req_id)` — anyone triggers a nondeterministic LLM prompt ("is this translation faithful? count major issues") through `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`. The validator re-runs the identical prompt and accepts only if both evaluations agree on the `faithful` boolean — mixed verdicts rotate leadership instead of settling. On agreement: both-true releases the reward to the translator's balance (`completed`); both-false refunds the requester (`refunded`). The stored outcome includes `faithful`, a non-negative `major_issues` count (i256-compatible int), and a reason.
5. Verification runs once; a second `verify` on the same request reverts with `[EXPECTED]` (no appeal flow in this scope).

## Contract interface

| Method | Kind | Description |
| --- | --- | --- |
| `top_up(amount_atto: u256)` / `withdraw()` | write | Internal ledger deposit / full withdrawal |
| `request_translation(req_id, source_text, target_lang, reward_atto)` | write | Requester funds a translation request |
| `accept(req_id)` | write | Lock one translator (requester self-accept blocked) |
| `submit_translation(req_id, translated_text)` | write | Translator-only submission |
| `verify(req_id) -> str` | write | Consensus AI verification: release or refund |
| `get_request(req_id) -> dict` | view | Full request record incl. verified outcome |
| `request_state(req_id) -> str` | view | `open`/`accepted`/`submitted`/`completed`/`refunded` |
| `my_balance(addr_str) -> u256` / `get_total_withdrawn() -> u256` / `get_owner() -> str` | view | Ledger stats / deployer |

## Quickstart

```bash
pip install -r requirements.txt
genvm-lint check contracts/TranslationVerifier.py --json
pytest tests/direct -v
gltest tests/integration -v -s --network studionet
genlayer network set studionet
genlayer deploy --contract contracts/TranslationVerifier.py --args []
```

Sample calls/writes:

```bash
genlayer write TranslationVerifier top_up --args '[1000000000000000000]'
genlayer write TranslationVerifier request_translation --args '["r1", "Hello world.", "French", 200000000000000000]'
genlayer write TranslationVerifier accept --args '["r1"]'
genlayer write TranslationVerifier submit_translation --args '["r1", "Bonjour le monde."]'
genlayer write TranslationVerifier verify --args '["r1"]'
genlayer call TranslationVerifier get_request --args '["r1"]'
genlayer call TranslationVerifier my_balance --args '["0x0000000000000000000000000000000000000000"]'
```

## Design notes

- **Equivalence principle.** All non-determinism lives in `_exec_verification()` (one `gl.nondet.exec_prompt` call returning `{"faithful": bool, "major_issues": int, "reason": str}`), executed by both leader and validator inside `gl.vm.run_nondet_unsafe`. The validator compares only the `faithful` decision boolean; issue counts and prose may legitimately vary run-to-run. Storage writes happen strictly after consensus returns.
- **Validator logic.** Non-`Return` leader results go through `_handle_leader_error`: exact message equality for deterministic `[EXPECTED]`/`[EXTERNAL]` failures, prefix agreement for `[TRANSIENT]`, rejection otherwise so leadership rotates on any disagreement (`faithful` mixed → rotation).
- **Error taxonomy.** `[EXPECTED]` guards (unknown/duplicate ids, insufficient funds, requester self-accept, submit before accept/locked-translator-only, verify before submission, double verify), `[EXTERNAL]` remote failures, `[TRANSIENT]` retryable failures, `[LLM_ERROR]` unusable model output — all raised as `gl.vm.UserError`.
- **Storage.** Class-level annotations only: `TreeMap[Address, u256]` ledger plus `TreeMap[str, str]` requests as JSON records (statuses are plain strings); addresses stored as strings in records and reconstructed with `Address(...)` only when moving balances.

## Limitations

- Money is an internal ledger demo scope: `top_up`/`withdraw` simulate deposits since native value transfers are out of scope here.
- Exactly one verification is stored per request — losing parties have no appeal path; a second `verify` intentionally reverts.
- Verification quality is one model opinion validated once by a rerun; borderline translations can rotate leadership instead of resolving.
