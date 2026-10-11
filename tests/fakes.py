"""A stand-in for the Anthropic client so extraction tests never touch the network."""

import json
from types import SimpleNamespace


def response(payload, stop_reason="end_turn", model="claude-sonnet-5-5"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model=model,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    )


class FakeClient:
    def __init__(self, *responses):
        self._queue = list(responses)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError("FakeClient called more times than expected")
        return self._queue.pop(0)
