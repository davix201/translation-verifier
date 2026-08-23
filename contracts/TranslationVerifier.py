# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *

import json
import re

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"
ERROR_TRANSIENT = "[TRANSIENT]"
ERROR_LLM = "[LLM_ERROR]"

STATUS_OPEN = "open"
STATUS_ACCEPTED = "accepted"
STATUS_SUBMITTED = "submitted"
STATUS_COMPLETED = "completed"
STATUS_REFUNDED = "refunded"

DECISION_KEY = "faithful"
DECISION_ALIASES = (DECISION_KEY, "is_faithful", "accurate", "verdict")
ISSUES_ALIASES = ("major_issues", "issues", "major_issue_count", "problem_count")
REASON_ALIASES = ("reason", "explanation", "rationale")

TRUE_WORDS = frozenset(("true", "yes", "ok", "approved", "pass", "passed", "1"))
FALSE_WORDS = frozenset(("false", "no", "reject", "rejected", "fail", "failed", "0"))


def _fail(message: str):
    raise gl.vm.UserError(message)


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip()[:limit]


def _pick(obj: dict, aliases):
    for name in aliases:
        if name in obj and obj[name] is not None:
            return obj[name]
    return None


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_WORDS:
            return True
        if lowered in FALSE_WORDS:
            return False
    _fail(f"{ERROR_LLM} cannot interpret decision value as bool")
    return False


def _coerce_int(value) -> int:
    if isinstance(value, bool):
        _fail(f"{ERROR_LLM} boolean where number expected")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        negative = text.startswith("-")
        digits = re.sub(r"[^0-9]", "", text)
        if digits == "":
            _fail(f"{ERROR_LLM} cannot coerce value to int")
        number = int(digits)
        return -number if negative else number
    _fail(f"{ERROR_LLM} cannot coerce value to int")
    return 0


def _json_object_text(out) -> str:
    if isinstance(out, dict):
        return json.dumps(out)
    if isinstance(out, (bytes, bytearray)):
        out = out.decode("utf-8", "ignore")
    if not isinstance(out, str):
        out = str(out)
    start = out.find("{")
    end = out.rfind("}")
    if start < 0 or end <= start:
        _fail(f"{ERROR_LLM} no JSON object found in model output")
    return out[start : end + 1]


def _strip_trailing_commas(fragment: str) -> str:
    return re.sub(r",(?!\s*?[\{\[\"\'\w])", "", fragment)


def _parse_model_json(out) -> dict:
    fragment = _json_object_text(out)
    cleaned = _strip_trailing_commas(fragment)
    parsed = None
    try:
        parsed = json.loads(cleaned)
    except Exception:
        try:
            parsed = json.loads(_strip_trailing_commas(fragment.replace("'", '"')))
        except Exception:
            _fail(f"{ERROR_LLM} could not parse model output as JSON")
    if not isinstance(parsed, dict):
        _fail(f"{ERROR_LLM} model output was not a JSON object")
    return parsed


def _same_faithfulness(first_json: str, second_json: str) -> bool:
    first = _coerce_bool(_pick(_parse_model_json(first_json), DECISION_ALIASES))
    second = _coerce_bool(_pick(_parse_model_json(second_json), DECISION_ALIASES))
    return first == second


def _handle_leader_error(leaders_res, leader_fn):
    leader_msg = leaders_res.message if hasattr(leaders_res, "message") else ""
    try:
        leader_fn(); return False
    except gl.vm.UserError as e:
        vm = e.message if hasattr(e, "message") else str(e)
        if vm.startswith(ERROR_EXPECTED) or vm.startswith(ERROR_EXTERNAL): return vm == leader_msg
        if vm.startswith(ERROR_TRANSIENT) and leader_msg.startswith(ERROR_TRANSIENT): return True
        return False
    except Exception: return False


def _verify_prompt(source_text: str, translated_text: str, target_lang: str) -> str:
    return (
        "You are an impartial translation verifier judging whether a "
        "translation faithfully conveys its source text.\n"
        "TARGET LANGUAGE: " + target_lang + "\n"
        "SOURCE TEXT:\n" + source_text + "\n"
        "TRANSLATED TEXT:\n" + translated_text + "\n"
        "Count MAJOR issues only: omissions, additions, or meaning changes that "
        "would mislead a reader; ignore stylistic preferences and minor wording.\n"
        "Respond ONLY with a single JSON object of the form "
        '{"faithful": <true|false>, "major_issues": <non-negative integer>, '
        '"reason": "<short explanation>"}'
    )


def _exec_verification(prompt: str) -> str:
    out = gl.nondet.exec_prompt(prompt, response_format="json")
    obj = _parse_model_json(out)
    faithful = _coerce_bool(_pick(obj, DECISION_ALIASES))
    major_issues = _coerce_int(_pick(obj, ISSUES_ALIASES))
    if major_issues < 0:
        major_issues = 0
    reason = _clip(_pick(obj, REASON_ALIASES), 400)
    return json.dumps({DECISION_KEY: faithful, "major_issues": major_issues, "reason": reason})


class TranslationVerifier(gl.Contract):
    """
    AI-arbitrated translation marketplace.

    A requester funds a translation request from their internal ledger
    balance, a different translator accepts and submits the translation,
    and anyone can trigger verification: an LLM judges faithfulness and a
    second independent evaluation must agree before the reward is released
    to the translator or refunded to the requester.
    """

    owner: Address
    deposits: TreeMap[Address, u256]
    requests: TreeMap[str, str]
    total_withdrawn: u256

    def __init__(self) -> None:
        self.owner = gl.message.sender_address
        self.total_withdrawn = u256(0)

    def _load_request(self, req_id: str) -> dict:
        key = str(req_id)
        raw = self.requests.get(key)
        if raw is None:
            _fail(f"{ERROR_EXPECTED} unknown request id {key}")
        return json.loads(raw)

    def _save_request(self, req_id: str, record: dict) -> None:
        self.requests[str(req_id)] = json.dumps(record)

    def _credit(self, who: Address, amount: int) -> None:
        current = int(self.deposits.get(who, u256(0)))
        self.deposits[who] = u256(current + amount)

    @gl.public.view
    def get_owner(self) -> str:
        return str(self.owner)

    @gl.public.view
    def get_request(self, req_id: str) -> dict:
        record = self._load_request(req_id)
        record["reward_atto"] = int(record["reward_atto"])
        record["major_issues"] = int(record["major_issues"])
        record["faithful"] = bool(record["faithful"]) if record["verified"] else False
        return record

    @gl.public.view
    def request_state(self, req_id: str) -> str:
        return self._load_request(req_id)["status"]

    @gl.public.view
    def my_balance(self, addr_str: str) -> u256:
        key = Address(str(addr_str).strip())
        return u256(self.deposits.get(key, u256(0)))

    @gl.public.view
    def get_total_withdrawn(self) -> u256:
        return self.total_withdrawn

    @gl.public.write
    def top_up(self, amount_atto: u256) -> None:
        amount = int(amount_atto)
        if amount <= 0:
            _fail(f"{ERROR_EXPECTED} top-up amount must be positive")
        sender = gl.message.sender_address
        current = int(self.deposits.get(sender, u256(0)))
        self.deposits[sender] = u256(current + amount)

    @gl.public.write
    def withdraw(self) -> None:
        sender = gl.message.sender_address
        balance = int(self.deposits.get(sender, u256(0)))
        if balance <= 0:
            _fail(f"{ERROR_EXPECTED} no balance to withdraw")
        self.deposits[sender] = u256(0)
        self.total_withdrawn = u256(int(self.total_withdrawn) + balance)

    @gl.public.write
    def request_translation(
        self,
        req_id: str,
        source_text: str,
        target_lang: str,
        reward_atto: u256,
    ) -> None:
        key = str(req_id)
        reward = int(reward_atto)
        if len(key.strip()) == 0:
            _fail(f"{ERROR_EXPECTED} request id must be non-empty")
        if self.requests.get(key) is not None:
            _fail(f"{ERROR_EXPECTED} request id {key} already exists")
        if len(source_text.strip()) == 0 or len(target_lang.strip()) == 0:
            _fail(f"{ERROR_EXPECTED} source text and target language must not be empty")
        if reward <= 0:
            _fail(f"{ERROR_EXPECTED} reward must be positive")
        requester = gl.message.sender_address
        balance = int(self.deposits.get(requester, u256(0)))
        if balance < reward:
            _fail(f"{ERROR_EXPECTED} insufficient balance: have {balance}, need {reward}")
        self.deposits[requester] = u256(balance - reward)
        record = {
            "id": key,
            "source_text": _clip(source_text, 8000),
            "target_lang": _clip(target_lang, 60),
            "reward_atto": reward,
            "requester": str(requester),
            "translator": "",
            "translated_text": "",
            "status": STATUS_OPEN,
            "faithful": False,
            "major_issues": 0,
            "reason": "",
            "verified": False,
        }
        self._save_request(key, record)

    @gl.public.write
    def accept(self, req_id: str) -> None:
        key = str(req_id)
        record = self._load_request(key)
        sender = gl.message.sender_address
        if record["status"] != STATUS_OPEN:
            _fail(f"{ERROR_EXPECTED} request {key} is not open (status '{record['status']}')")
        if sender == Address(record["requester"]):
            _fail(f"{ERROR_EXPECTED} the requester cannot accept their own request")
        record["translator"] = str(sender)
        record["status"] = STATUS_ACCEPTED
        self._save_request(key, record)

    @gl.public.write
    def submit_translation(self, req_id: str, translated_text: str) -> None:
        key = str(req_id)
        record = self._load_request(key)
        sender = gl.message.sender_address
        if record["translator"] == "" or sender != Address(record["translator"]):
            _fail(f"{ERROR_EXPECTED} only the locked translator can submit the translation")
        if record["status"] != STATUS_ACCEPTED:
            _fail(f"{ERROR_EXPECTED} request {key} is not awaiting a translation (status '{record['status']}')")
        if len(translated_text.strip()) == 0:
            _fail(f"{ERROR_EXPECTED} translated text must not be empty")
        record["translated_text"] = _clip(translated_text, 12000)
        record["status"] = STATUS_SUBMITTED
        self._save_request(key, record)

    @gl.public.write
    def verify(self, req_id: str) -> str:
        key = str(req_id)
        record = self._load_request(key)
        status = record["status"]
        if status == STATUS_COMPLETED or status == STATUS_REFUNDED:
            _fail(f"{ERROR_EXPECTED} request {key} was already verified (appeals are not supported)")
        if status != STATUS_SUBMITTED:
            _fail(f"{ERROR_EXPECTED} request {key} has no translation to verify (status '{status}')")
        prompt = _verify_prompt(
            record["source_text"],
            record["translated_text"],
            record["target_lang"],
        )

        def leader_fn() -> str:
            return _exec_verification(prompt)

        def validator_fn(leaders_res) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return _handle_leader_error(leaders_res, leader_fn)
            try:
                return _same_faithfulness(leaders_res.calldata, leader_fn())
            except Exception:
                return False

        result_json = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        verdict = _parse_model_json(result_json)
        faithful = _coerce_bool(_pick(verdict, DECISION_ALIASES))
        major_issues = _coerce_int(_pick(verdict, ISSUES_ALIASES))
        if major_issues < 0:
            major_issues = 0
        reason = _clip(_pick(verdict, REASON_ALIASES), 400)
        reward = int(record["reward_atto"])
        if faithful:
            translator = Address(record["translator"])
            self._credit(translator, reward)
            record["status"] = STATUS_COMPLETED
        else:
            requester = Address(record["requester"])
            self._credit(requester, reward)
            record["status"] = STATUS_REFUNDED
        record["faithful"] = faithful
        record["major_issues"] = major_issues
        record["reason"] = reason
        record["verified"] = True
        self._save_request(key, record)
        return json.dumps(
            {
                "req_id": key,
                DECISION_KEY: faithful,
                "major_issues": major_issues,
                "status": record["status"],
                "reason": reason,
            }
        )
