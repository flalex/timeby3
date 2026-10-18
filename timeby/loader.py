"""Load and validate contracts. A contract is a JSON file in timeby/contracts/; add a file to add a contract."""
from __future__ import annotations

import json
from pathlib import Path

from .model import Contract, Nudge, Pair, Phase, Rule, RuleKind, Window

CONTRACTS_DIR = Path(__file__).parent / "contracts"
ACTIONS_FILE = Path(__file__).parent / "actions.json"


class ContractError(ValueError):
    pass


def load_actions(path: Path = ACTIONS_FILE) -> dict[str, str]:
    return json.loads(path.read_text())


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def load_contracts(directory: Path = CONTRACTS_DIR, actions: dict[str, str] | None = None) -> list[Contract]:
    actions = actions if actions is not None else load_actions()
    contracts = [parse_contract(json.loads(p.read_text()), actions) for p in sorted(directory.glob("*.json"))]
    rule_ids = [r.id for c in contracts for r in c.rules]
    duplicates = {i for i in rule_ids if rule_ids.count(i) > 1}
    if duplicates:  # events can be tied to a goal by rule id, so ids must be unique across contracts
        raise ContractError(f"rule ids must be unique across contracts: {sorted(duplicates)}")
    dangling = [f"{r.id} -> {p.with_rule}" for c in contracts for r in c.rules for p in r.pairs if p.with_rule not in rule_ids]
    if dangling:
        raise ContractError(f"pairs point to unknown goals: {dangling}")
    return contracts


def parse_contract(raw: dict, actions: dict[str, str]) -> Contract:
    contract = Contract(
        id=raw["id"],
        name=raw["name"],
        domain=raw["domain"],
        goal=raw["goal"],
        phases=tuple(Phase(p["id"], p["goal"], _window(p["window"])) for p in raw["phases"]),
        rules=tuple(_rule(r) for r in raw["rules"]),
        sources=raw["sources"],
    )
    _validate(contract, actions)
    return contract


def _window(raw: dict) -> Window:
    return Window(raw["anchor"], raw["start"], raw["end"])


def _rule(raw: dict) -> Rule:
    nudge = raw.get("nudge")
    return Rule(
        id=raw["id"],
        title=raw["title"],
        phase=raw["phase"],
        kind=RuleKind(raw["kind"]),
        actions=tuple(raw["actions"]),
        window=_window(raw["window"]),
        weight=float(raw["weight"]),
        strength=raw["strength"],
        why=raw["why"],
        advice=raw["advice"],
        sources=tuple(raw["sources"]),
        nudge=Nudge(nudge["at"], nudge["text"]) if nudge else None,
        tolerance=raw.get("tolerance", 0),
        lookback_days=raw.get("lookback_days", 7),
        label=raw.get("label", raw["title"]),
        duration=raw.get("duration"),
        reopens=raw.get("reopens"),
        days=tuple(raw.get("days", ())),
        topic=raw.get("topic", ""),
        pairs=tuple(Pair(p["with"], p["text"]) for p in raw.get("pairs", ())),
        late_credit=float(raw.get("late_credit", 0.5)),
        remind=bool(raw.get("remind", True)),
    )


def _validate(contract: Contract, actions: dict[str, str]) -> None:
    phase_ids = {p.id for p in contract.phases}
    errors = []
    for rule in contract.rules:
        where = f"{contract.id}.{rule.id}"
        errors += [f"{where}: unknown action '{a}'" for a in rule.actions if a not in actions]
        errors += [f"{where}: unknown source '{s}'" for s in rule.sources if s not in contract.sources]
        if not rule.sources:
            errors.append(f"{where}: every rule must cite at least one source")
        if rule.phase not in phase_ids:
            errors.append(f"{where}: unknown phase '{rule.phase}'")
        if rule.window.start >= rule.window.end:
            errors.append(f"{where}: window start must be before end")
        if rule.kind is RuleKind.REGULARITY and rule.tolerance <= 0:
            errors.append(f"{where}: regularity rule needs a positive tolerance")
        errors += [f"{where}: unknown weekday '{d}'" for d in rule.days if d not in WEEKDAYS]
        if rule.strength not in {"strong", "moderate", "emerging"}:
            errors.append(f"{where}: strength must be strong|moderate|emerging")
    if errors:
        raise ContractError("\n".join(errors))
