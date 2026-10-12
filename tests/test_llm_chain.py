"""The "auto" provider chain: Groq -> SambaNova -> Ollama, nothing else.

Every provider call is replaced by a stub that records it, so these tests pin
the order and the fall-through without any network.
"""
import pytest

from llm import llm_client


@pytest.fixture
def calls(monkeypatch):
    """Stub the three providers; each fails unless named in `answering`."""
    order = []
    state = {"answering": set()}

    def stub(name):
        def call(prompt, system_prompt=None):
            order.append(name)
            if name in state["answering"]:
                return f"{name} answer"
            raise RuntimeError(f"{name} down")

        return call

    for name in ("groq", "sambanova", "ollama"):
        monkeypatch.setattr(llm_client, f"_call_{name}", stub(name))
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "auto")
    return order, state


def test_cerebras_is_gone():
    assert not hasattr(llm_client, "_call_cerebras")
    assert not any(name.startswith("CEREBRAS") for name in vars(llm_client))


def test_chain_order_is_groq_sambanova_ollama(calls):
    order, state = calls
    state["answering"] = {"ollama"}
    assert llm_client.get_llm_response("q") == "ollama answer"
    assert order == ["groq", "sambanova", "ollama"]


def test_sambanova_answers_when_groq_fails(calls):
    order, state = calls
    state["answering"] = {"sambanova"}
    assert llm_client.get_llm_response("q") == "sambanova answer"
    assert order == ["groq", "sambanova"]


def test_groq_answering_stops_the_chain(calls):
    order, state = calls
    state["answering"] = {"groq", "sambanova", "ollama"}
    assert llm_client.get_llm_response("q") == "groq answer"
    assert order == ["groq"]


def test_last_error_is_raised_when_every_provider_fails(calls):
    order, _ = calls
    with pytest.raises(RuntimeError, match="ollama down"):
        llm_client.get_llm_response("q")
    assert order == ["groq", "sambanova", "ollama"]


@pytest.mark.parametrize("provider", ["groq", "sambanova", "ollama"])
def test_a_named_provider_is_called_alone(calls, monkeypatch, provider):
    order, state = calls
    state["answering"] = {provider}
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", provider)
    assert llm_client.get_llm_response("q") == f"{provider} answer"
    assert order == [provider]
