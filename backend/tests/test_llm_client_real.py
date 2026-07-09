import os

import pytest

from backend.core.config import get_settings
from backend.llm.client import LLMMessage, create_llm_client


@pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS") != "1",
    reason="Real LLM tests are disabled.",
)
def test_real_llm_client_smoke():
    settings = get_settings()
    client = create_llm_client(settings)

    response = client.generate(
        messages=[
            LLMMessage(
                role="user",
                content="Return exactly: hello",
            )
        ],
        temperature=0.0,
        max_tokens=20,
    )

    assert response.content