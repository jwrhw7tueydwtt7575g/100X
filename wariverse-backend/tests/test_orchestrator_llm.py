"""The gpt-4o tool-calling loop, driven by a scripted fake client.

No API key is needed: `openai.AsyncOpenAI` is replaced with a stub that returns
a prepared sequence of responses. This is the only way to check the part that
matters most — that a tool the model asks for is actually executed, that its
result is fed back, and that the widget the app renders comes from real service
output rather than from the model's prose.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import settings
from app.models.schemas import GeoPoint
from app.services.llm_orchestrator import (
    TOOL_SCHEMAS,
    LLMOrchestrator,
    build_system_prompt,
)
from tests.conftest import TEMPLE_LAT, TEMPLE_LON


# --- fake OpenAI ------------------------------------------------------------


def tool_call(name: str, arguments: dict, call_id: str = "call_1") -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def response(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))]
    )


class FakeOpenAI:
    """Returns queued responses and records every request it was given."""

    def __init__(self, script: list[SimpleNamespace]) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            raise AssertionError("the model was called more times than scripted")
        return self.script.pop(0)


@pytest.fixture
def with_llm(monkeypatch):
    """Enable the LLM path and install a scripted client."""

    def install(script: list[SimpleNamespace]) -> FakeOpenAI:
        fake = FakeOpenAI(script)
        monkeypatch.setattr(settings, "openai_api_key", "test-key", raising=False)
        monkeypatch.setattr(settings, "llm_enabled", True, raising=False)

        import openai

        monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake)
        return fake

    return install


# --- the loop ---------------------------------------------------------------


async def test_tool_is_executed_and_its_result_fed_back(with_llm) -> None:
    fake = with_llm(
        [
            response(tool_calls=[tool_call("get_crowd_density", {"zone_id": "gate-2"})]),
            response(content="Gate 2 is busy right now — try Gate 3 instead."),
        ]
    )

    result = await LLMOrchestrator(None).process_message(
        user_message="how busy is gate 2?", language="en"
    )

    assert result.source == "llm"
    assert result.response_text == "Gate 2 is busy right now — try Gate 3 instead."
    assert result.tools_called == ["get_crowd_density"]

    # The widget carries real service output, not anything the model wrote.
    assert [w["type"] for w in result.widgets] == ["crowd_density"]
    assert result.widgets[0]["data"]["zone_id"] == "gate-2"

    # Second call must include the tool result as a `tool` message.
    second = fake.calls[1]["messages"]
    tool_messages = [m for m in second if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"

    payload = json.loads(tool_messages[0]["content"])
    assert payload["status"] in ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
    assert "density_percent" in payload


async def test_several_tools_in_one_turn(with_llm) -> None:
    with_llm(
        [
            response(
                tool_calls=[
                    tool_call("get_crowd_density", {"zone_id": "temple-main"}, "a"),
                    tool_call("get_temple_info", {}, "b"),
                ]
            ),
            response(content="The queue is moving; darshan runs from 4am."),
        ]
    )

    result = await LLMOrchestrator(None).process_message(
        user_message="how is the temple right now and when is darshan?", language="en"
    )
    assert result.tools_called == ["get_crowd_density", "get_temple_info"]
    assert [w["type"] for w in result.widgets] == ["crowd_density", "temple_info"]


async def test_model_answering_without_a_tool_is_passed_through(with_llm) -> None:
    with_llm([response(content="Ram Krishna Hari! How can I help?")])

    result = await LLMOrchestrator(None).process_message(user_message="hello", language="en")
    assert result.response_text == "Ram Krishna Hari! How can I help?"
    assert result.widgets == []


async def test_tool_arguments_that_are_not_json_do_not_crash(with_llm) -> None:
    broken = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="get_crowd_density", arguments="{not json"),
    )
    with_llm([response(tool_calls=[broken]), response(content="Here is the temple area.")])

    result = await LLMOrchestrator(None).process_message(user_message="crowd?", language="en")
    # Falls back to the default zone rather than failing the turn.
    assert result.widgets[0]["data"]["zone_id"] == "temple-main"


async def test_hallucinated_zone_is_refused_and_reported_to_the_model(with_llm) -> None:
    fake = with_llm(
        [
            response(tool_calls=[tool_call("get_crowd_density", {"zone_id": "atlantis"})]),
            response(content="I don't have data for that place."),
        ]
    )

    result = await LLMOrchestrator(None).process_message(user_message="crowd at atlantis?", language="en")
    assert result.widgets == []

    tool_message = [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"][0]
    payload = json.loads(tool_message["content"])
    assert "error" in payload
    assert "known_zones" in payload  # the model is told what it may ask for


async def test_unknown_tool_name_is_survivable(with_llm) -> None:
    fake = with_llm(
        [
            response(tool_calls=[tool_call("book_a_taxi", {})]),
            response(content="I can't do that, but I can help with the walk."),
        ]
    )

    result = await LLMOrchestrator(None).process_message(user_message="book me a taxi", language="en")
    assert result.response_text == "I can't do that, but I can help with the walk."
    assert result.widgets == []
    assert "error" in json.loads(
        [m for m in fake.calls[1]["messages"] if m.get("role") == "tool"][0]["content"]
    )


async def test_endless_tool_calling_is_capped(with_llm) -> None:
    # A model that only ever calls tools must not loop forever.
    with_llm(
        [response(tool_calls=[tool_call("get_temple_info", {}, f"c{i}")]) for i in range(3)]
    )

    result = await LLMOrchestrator(None).process_message(user_message="temple?", language="en")
    # Falls back to a deterministic description of what the tools returned.
    assert result.source == "rules"
    assert result.response_text
    assert [w["type"] for w in result.widgets] == ["temple_info"] * 3


async def test_tool_results_survive_a_model_that_never_finishes(with_llm) -> None:
    """A filed report's reference number must reach the pilgrim regardless.

    If the model calls a tool and then dies before writing a sentence, the
    tool's result — here a real crowd reading — must still be described and
    its widget returned, not thrown away.
    """
    with_llm(
        [
            response(tool_calls=[tool_call("get_crowd_density", {"zone_id": "gate-1"}, "x")]),
            response(tool_calls=[tool_call("get_crowd_density", {"zone_id": "gate-1"}, "y")]),
            response(tool_calls=[tool_call("get_crowd_density", {"zone_id": "gate-1"}, "z")]),
        ]
    )

    result = await LLMOrchestrator(None).process_message(user_message="crowd?", language="en")
    assert result.response_text  # a real sentence, not an empty string
    assert result.widgets[0]["data"]["zone_id"] == "gate-1"


async def test_llm_failure_falls_back_to_the_keyword_router(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "test-key", raising=False)
    monkeypatch.setattr(settings, "llm_enabled", True, raising=False)

    class Exploding:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._boom))

        async def _boom(self, **kwargs):
            raise RuntimeError("openai is down")

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: Exploding())

    result = await LLMOrchestrator(None).process_message(
        user_message="how crowded is gate-2?", language="en"
    )
    # The pilgrim still gets a grounded answer and a widget.
    assert result.source == "rules"
    assert [w["type"] for w in result.widgets] == ["crowd_density"]


# --- prompt and channel -----------------------------------------------------


def test_ivr_prompt_forbids_lists_and_caps_length() -> None:
    prompt = build_system_prompt("mr", "ivr", None, None)
    assert "MAXIMUM two sentences" in prompt
    assert "No lists" in prompt
    assert "Marathi" in prompt


def test_app_prompt_expects_cards_not_readback() -> None:
    prompt = build_system_prompt("en", "app", None, None)
    assert "card" in prompt.lower()
    assert "MAXIMUM two sentences" not in prompt


def test_prompt_carries_the_pilgrims_location() -> None:
    prompt = build_system_prompt(
        "en", "app", GeoPoint(lat=TEMPLE_LAT, lon=TEMPLE_LON), None
    )
    assert str(TEMPLE_LAT) in prompt
    assert "location is unknown" not in prompt


def test_prompt_says_so_when_location_is_missing() -> None:
    prompt = build_system_prompt("en", "app", None, None)
    assert "location is unknown" in prompt


def test_prompt_never_invents_facts() -> None:
    prompt = build_system_prompt("hi", "app", None, None)
    assert "Never make up crowd data" in prompt
    assert "Never invent a phone number" in prompt


async def test_ivr_asks_the_model_for_a_shorter_reply(with_llm) -> None:
    fake = with_llm([response(content="Gate two is busy. Try gate three.")])

    result = await LLMOrchestrator(None).process_message(
        user_message="how busy is gate 2?", language="en", channel="ivr"
    )
    assert fake.calls[0]["max_tokens"] < 200
    assert result.widgets == []  # a phone call has no screen


# --- tool schemas -----------------------------------------------------------


def test_all_nine_tools_are_exposed() -> None:
    names = {schema["function"]["name"] for schema in TOOL_SCHEMAS}
    assert names == {
        "get_crowd_density",
        "get_congestion_forecast",
        "get_route_guidance",
        "get_nearby_facility",
        "get_temple_info",
        "report_lost_found",
        "trigger_sos",
        "get_palkhi_location",
        "escalate_to_human",
    }


def test_tool_schemas_are_well_formed() -> None:
    for schema in TOOL_SCHEMAS:
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["description"], function["name"]

        params = function["parameters"]
        assert params["type"] == "object"
        for required in params.get("required", []):
            assert required in params["properties"], function["name"]


def test_zone_parameters_are_constrained_to_real_zones() -> None:
    from app.data.reference import ZONES_BY_ID

    for schema in TOOL_SCHEMAS:
        zone = schema["function"]["parameters"]["properties"].get("zone_id")
        if zone:
            # An enum is what stops the model inventing a zone id.
            assert set(zone["enum"]) == set(ZONES_BY_ID)


# --- tool behaviour ---------------------------------------------------------


async def test_trigger_sos_never_activates_on_its_own() -> None:
    orchestrator = LLMOrchestrator(None)
    state = await orchestrator.sessions.resolve(str(uuid4()))

    outcome = await orchestrator._execute_tool(
        "trigger_sos",
        {"lat": TEMPLE_LAT, "lng": TEMPLE_LON, "emergency_type": "medical"},
        language="en",
        state=state,
        point=None,
    )
    # The model can raise an emergency; only a human confirmation dispatches one.
    assert outcome.widget["data"]["status"] == "CONFIRMATION_REQUIRED"
    assert outcome.summary["status"] == "CONFIRMATION_REQUIRED"


async def test_forecast_points_are_bounded_and_ordered() -> None:
    outcome = await LLMOrchestrator(None)._execute_tool(
        "get_congestion_forecast",
        {"zone_id": "bhima-ghat", "hours": 6},
        language="en",
        state=None,
        point=None,
    )
    points = outcome.widget["data"]["points"]
    assert len(points) == 6
    assert all(0 <= p["value"] <= 100 for p in points)
    assert outcome.summary["note"].startswith("projection")


async def test_forecast_hours_are_clamped() -> None:
    outcome = await LLMOrchestrator(None)._execute_tool(
        "get_congestion_forecast",
        {"zone_id": "gate-1", "hours": 999},
        language="en",
        state=None,
        point=None,
    )
    assert len(outcome.widget["data"]["points"]) == 24  # capped at a day


async def test_nearby_facility_emits_one_widget_per_place() -> None:
    outcome = await LLMOrchestrator(None)._execute_tool(
        "get_nearby_facility",
        {"lat": TEMPLE_LAT, "lng": TEMPLE_LON, "category": "water"},
        language="en",
        state=None,
        point=None,
    )
    assert len(outcome.widgets) >= 2
    assert all(w["type"] == "nearby_facility" for w in outcome.widgets)
    distances = [float(w["data"]["distance"].split()[0]) for w in outcome.widgets]
    assert distances == sorted(distances)


async def test_report_lost_found_without_a_database_tells_the_pilgrim_to_call() -> None:
    outcome = await LLMOrchestrator(None)._execute_tool(
        "report_lost_found",
        {
            "incident_type": "PERSON",
            "description": "Boy aged 7, yellow shirt, last seen at Gate 2",
            "reporter_phone": "9876543210",
        },
        language="en",
        state=None,
        point=None,
    )
    assert outcome.ok is False
    assert outcome.widget["data"]["status"] == "FAILED"
    # A lost child must never end in silence — the helpline has to be surfaced.
    assert "1800-233-1000" in outcome.widget["data"]["next_action"]
