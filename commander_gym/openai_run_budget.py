"""Conservative, persistent GPT-6 Luna request budget for local game-server runs.

The ledger is shared by all seats and survives process restarts. It stores counts and
cost estimates only; request bodies, model responses, and credentials stay in the
existing private provenance flow. SDK transport retries must be disabled by the caller.
"""

from __future__ import annotations

import fcntl
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping


class OpenAIRunBudgetError(RuntimeError):
    """Raised before a request if its conservative reservation exceeds the cap."""


class OpenAIRunBudget:
    INPUT_USD_PER_MILLION = 0.10
    OUTPUT_USD_PER_MILLION = 0.50
    # Covers long-context pricing and a possible regional/Fast surcharge.
    SAFETY_MULTIPLIER = 2.2
    MAX_OUTPUT_TOKENS = 2048

    def __init__(
        self, path: Path, cap_usd: float, *, authorized_max_usd: float = 5.0,
        max_requests: int | None = None,
    ) -> None:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("budget ledger path must be absolute")
        if (
            type(cap_usd) not in (int, float) or not math.isfinite(cap_usd)
            or type(authorized_max_usd) not in (int, float)
            or not math.isfinite(authorized_max_usd)
            or cap_usd <= 0 or authorized_max_usd <= 0
            or cap_usd > authorized_max_usd
        ):
            raise ValueError("run budget must be positive and within the authorized ceiling")
        if max_requests is not None and (type(max_requests) is not int or max_requests < 0):
            raise ValueError("max_requests must be a nonnegative absolute ledger count")
        if not path.parent.is_dir():
            raise ValueError("budget ledger parent must exist")
        self.path = path
        self.cap_usd = float(cap_usd)
        self.authorized_max_usd = float(authorized_max_usd)
        self.max_requests = max_requests

    def _transact(
        self, update: Callable[[dict[str, Any]], Any], *, require_existing: bool = False,
    ) -> Any:
        with self.path.open("a+", encoding="utf-8") as ledger:
            fcntl.flock(ledger.fileno(), fcntl.LOCK_EX)
            ledger.seek(0)
            content = ledger.read()
            if require_existing and not content:
                raise OpenAIRunBudgetError("cap increase requires an existing ledger")
            if not content and self.cap_usd > 5:
                raise OpenAIRunBudgetError("a new ledger cannot start above the default $5 cap")
            data = json.loads(content) if content else {
                "schemaVersion": 1,
                "capUsd": self.cap_usd,
                "estimatedUsd": 0.0,
                "requests": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "unsettledRequests": 0,
            }
            if data.get("schemaVersion") != 1 or data.get("capUsd") != self.cap_usd:
                raise OpenAIRunBudgetError("budget ledger schema or cap does not match this run")
            result = update(data)
            ledger.seek(0)
            ledger.truncate()
            json.dump(data, ledger, sort_keys=True)
            ledger.flush()
            return result

    def snapshot(self) -> dict[str, Any]:
        return self._transact(lambda data: dict(data))

    def increase_cap(self, new_cap_usd: float) -> dict[str, Any]:
        """Explicitly raise an existing ledger cap within a separately set ceiling.

        Call only while run processes are stopped, after fresh spending authority.
        The locked update preserves requests, token totals, and unsettled reserves.
        """
        if (
            type(new_cap_usd) not in (int, float) or not math.isfinite(new_cap_usd)
            or new_cap_usd <= self.cap_usd
            or new_cap_usd > self.authorized_max_usd
        ):
            raise ValueError("new cap must exceed the current cap within the authorized ceiling")
        new_cap = float(new_cap_usd)

        def update(data: dict[str, Any]) -> dict[str, Any]:
            data["capUsd"] = new_cap
            return dict(data)

        result = self._transact(update, require_existing=True)
        self.cap_usd = new_cap
        return result

    @classmethod
    def _cost(cls, input_tokens: int, output_tokens: int) -> float:
        return cls.SAFETY_MULTIPLIER * (
            input_tokens * cls.INPUT_USD_PER_MILLION
            + output_tokens * cls.OUTPUT_USD_PER_MILLION
        ) / 1_000_000

    def create(self, create: Callable[..., Any], request: Mapping[str, Any]) -> Any:
        if request.get("model") != "gpt-6-luna":
            raise OpenAIRunBudgetError("the bounded run supports only the qualified GPT-6 Luna model")
        if request.get("max_output_tokens") != self.MAX_OUTPUT_TOKENS:
            raise OpenAIRunBudgetError("bounded request is missing its output-token cap")
        # A byte per input token plus framing margin over-reserves normal text and
        # includes the request-local schema. Failed/ambiguous attempts keep that
        # reservation because the provider may have processed them.
        request_bytes = len(json.dumps(request, ensure_ascii=False).encode("utf-8"))
        reserved = self._cost(request_bytes + 4096, self.MAX_OUTPUT_TOKENS)

        def reserve(data: dict[str, Any]) -> None:
            if self.max_requests is not None and data["requests"] >= self.max_requests:
                raise OpenAIRunBudgetError("absolute request limit reached before dispatch")
            if data["estimatedUsd"] + reserved > self.cap_usd:
                raise OpenAIRunBudgetError("the next OpenAI request exceeds the cumulative run cap")
            data["estimatedUsd"] += reserved
            data["requests"] += 1
            data["unsettledRequests"] += 1

        self._transact(reserve)
        response = create(**request)
        usage = getattr(response, "usage", None)
        input_tokens = (
            usage.get("input_tokens") if isinstance(usage, Mapping)
            else getattr(usage, "input_tokens", None)
        )
        output_tokens = (
            usage.get("output_tokens") if isinstance(usage, Mapping)
            else getattr(usage, "output_tokens", None)
        )
        if type(input_tokens) is int and type(output_tokens) is int:
            actual = self._cost(input_tokens, output_tokens)
            if actual > reserved:
                raise OpenAIRunBudgetError("provider usage exceeded conservative reservation")

            def settle(data: dict[str, Any]) -> None:
                data["estimatedUsd"] -= reserved - actual
                data["inputTokens"] += input_tokens
                data["outputTokens"] += output_tokens
                data["unsettledRequests"] -= 1

            self._transact(settle)
        return response
