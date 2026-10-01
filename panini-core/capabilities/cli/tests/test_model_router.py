from panini_cli.agent import model_router


def options():
    return [
        model_router.ModelOption("gpt-cheap", "cheap", "cheap"),
        model_router.ModelOption("gpt-balanced", "balanced", "balanced"),
        model_router.ModelOption("gpt-strong", "strong", "strong"),
    ]


def test_model_router_picks_cheapest_confident_sufficient_model():
    decision = model_router.decide(
        prompt="format this file",
        current_model="gpt-balanced",
        answers={
            "model_0": {"probability": 0.91},
            "model_1": {"probability": 0.60},
            "model_2": {"probability": 0.20},
        },
        options=options(),
    )
    assert decision.model == "gpt-cheap"
    assert decision.reason == "jev"
    assert decision.changed


def test_model_router_does_not_downgrade_on_low_confidence():
    decision = model_router.decide(
        prompt="fix the bug",
        current_model="gpt-strong",
        answers={
            "model_0": {"probability": 0.29},
            "model_1": {"probability": 0.20},
            "model_2": {"probability": 0.10},
        },
        options=options(),
    )
    assert decision.model == "gpt-strong"
    assert decision.reason == "low-confidence-no-downgrade/no-change"


def test_model_router_does_not_use_cheapest_model_for_local_machine_tasks():
    decision = model_router.decide(
        prompt="check downloads folder",
        current_model="gpt-cheap",
        answers={
            "model_0": {"probability": 0.91},
            "model_1": {"probability": 0.50},
            "model_2": {"probability": 0.20},
        },
        options=options(),
    )
    assert decision.model == "gpt-balanced"
    assert decision.reason == "local-machine-floor"


def test_model_router_respects_explicit_human_override():
    decision = model_router.decide(
        prompt="use gpt-strong and audit the sandbox",
        current_model="gpt-balanced",
        answers={"model_0": {"probability": 0.99}},
        options=options(),
    )
    assert decision.model == "gpt-strong"
    assert decision.reason == "override"


def test_model_router_models_are_configurable(monkeypatch):
    monkeypatch.setenv(
        "PANINI_CLI_ROUTER_MODELS",
        "gpt-x:nano is invalid tier,gpt-y:frontier:deep release work",
    )
    parsed = model_router.model_options()
    assert [option.id for option in parsed] == ["gpt-x", "gpt-y"]
    assert parsed[0].tier == "strong"
    assert parsed[1].tier == "frontier"
