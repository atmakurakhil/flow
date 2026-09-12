import pytest

from browser import _selector, _thread_id, _validated_url, browser_press


def test_browser_urls_must_be_complete_http_urls():
    assert _validated_url("https://example.com/a") == "https://example.com/a"

    with pytest.raises(ValueError, match="complete http"):
        _validated_url("example.com")

    with pytest.raises(ValueError, match="complete http"):
        _validated_url("file:///etc/passwd")


def test_browser_session_comes_from_the_graph_thread():
    assert _thread_id({"configurable": {"thread_id": "thread-123"}}) == "thread-123"
    assert _thread_id({}) == "direct-run"


def test_browser_refs_cannot_target_another_session():
    class Session:
        references = {"1": "[data-flow-browser-ref='flow-browser-1']"}

    assert _selector(Session(), "ref:1") == "[data-flow-browser-ref='flow-browser-1']"
    with pytest.raises(ValueError, match="Unknown browser reference"):
        _selector(Session(), "ref:2")


def test_browser_press_hides_injected_runnable_config_from_the_model_schema():
    schema = browser_press.args_schema.model_json_schema()
    assert set(schema["properties"]) == {"key", "target"}
