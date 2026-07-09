import os

import pytest
from dotenv import load_dotenv

from app.extraction.llm_extractor import build_llm_client, extract_candidate_profile

load_dotenv()


def test_real_openai_extraction_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.getenv("RUN_REAL_LLM_SMOKE") != "1":
        pytest.skip("Set RUN_REAL_LLM_SMOKE=1 to run the real LLM smoke test.")
    if not os.getenv("OPENAI_EXTRACT_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        pytest.skip("Set OPENAI_EXTRACT_API_KEY or OPENAI_API_KEY to run the real LLM smoke test.")
    if not os.getenv("OPENAI_EXTRACT_MODEL") and not os.getenv("OPENAI_MODEL"):
        monkeypatch.setenv("OPENAI_EXTRACT_MODEL", "gpt-4o-mini")

    resume_text = """Morgan Demo
morgan.demo@example.com
Location: Singapore
LinkedIn: https://www.linkedin.com/in/morgan-demo
Professional Summary
Backend engineer focused on data platforms and reliable APIs.
Skills
Python, FastAPI, SQL
Experience
Senior Backend Engineer at Synthetic Systems (2021 - Present)
- Built internal data services
- Improved API reliability
Education
BSc Computer Science - Example University (2018)
"""

    profile = extract_candidate_profile(
        resume_text,
        build_llm_client(provider="openai", stage="extract"),
    )

    assert profile.full_name == "Morgan Demo"
    assert profile.email == "morgan.demo@example.com"
    assert "Python" in profile.skills
    assert {field.field_name for field in profile.missing_fields} >= {
        "salary_expectation",
        "notice_period",
        "work_authorization",
        "interview_availability",
    }
