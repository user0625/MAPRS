from backend.agents.metadata_extractor_agent import (
    ExtractedMetadata,
    MetadataExtractionInput,
    MetadataExtractorAgent,
)
from backend.agents.verifier_agent import VerifierAgent, VerifierInput, VerifierResult
from backend.llm.client import MockLLMClient
from backend.schemas.agent_io import EvidenceBundle, EvidenceItem
from backend.schemas.paper import PaperMetadata
from backend.schemas.paper import MetadataCandidate
from backend.core.orchestrator import PaperAnalysisOrchestrator
from backend.schemas.report import FinalReport, ReportSection


class StructuredStubClient(MockLLMClient):
    def __init__(self, result):
        super().__init__(provider="openai_compatible")
        self.result = result
        self.last_prompt = ""

    def generate_pydantic(self, prompt, output_schema, **kwargs):
        self.last_prompt = prompt
        assert isinstance(self.result, output_schema)
        return self.result


def test_metadata_extractor_uses_limited_context_and_structured_output():
    result = ExtractedMetadata(title="A Reliable Title", authors=["Alice", "Bob"],
                               confidence={"title": 0.82, "authors": 0.76})
    client = StructuredStubClient(result)
    agent = MetadataExtractorAgent(client)
    output = agent.run(MetadataExtractionInput(
        current_metadata=PaperMetadata(), first_page_text="A Reliable Title\nAlice, Bob",
        abstract_candidate="Short abstract candidate.", section_candidates=["1 Introduction"],
        requested_fields=["title", "authors"],
    ))
    assert output.title == "A Reliable Title"
    assert "A Reliable Title" in client.last_prompt
    assert "source_path" not in client.last_prompt


def test_verifier_returns_scores_and_never_sends_unrelated_chunks():
    result = VerifierResult(accuracy=90, completeness=80, faithfulness=90,
        citation_validity=100, critical_depth=75, overall=87, passed=True)
    client = StructuredStubClient(result)
    agent = VerifierAgent(client)
    report = FinalReport(sections=[ReportSection(title="Method", content="Supported.",
                                                 evidence_ids=["ev_1"])])
    evidence = EvidenceBundle(items=[EvidenceItem(evidence_id="ev_1", chunk_id="chunk_1",
        paper_id="paper_1", text="Supporting evidence", page_start=2, page_end=2)])
    output = agent.run(VerifierInput(report=report, evidence_bundle=evidence))
    assert output.passed
    assert "Supporting evidence" in client.last_prompt
    assert "ev_1" in client.last_prompt


def test_llm_adjudication_rejects_invented_or_rotated_header_values():
    metadata = PaperMetadata(candidates=[
        MetadataCandidate(text="A Grounded Paper Title", font_size=14, rotation=0),
        MetadataCandidate(text="Alice Smith, Bob Jones", font_size=10, rotation=0),
        MetadataCandidate(text="arXiv:2307.11952v1 [cs.CV]", font_size=20, rotation=90),
    ])
    assert PaperAnalysisOrchestrator._candidate_supported(
        "title", "A Grounded Paper Title", metadata
    )
    assert PaperAnalysisOrchestrator._candidate_supported(
        "authors", ["Alice Smith", "Bob Jones"], metadata
    )
    assert not PaperAnalysisOrchestrator._candidate_supported(
        "title", "arXiv:2307.11952v1 [cs.CV]", metadata
    )
    assert not PaperAnalysisOrchestrator._candidate_supported(
        "title", "An Invented Title", metadata
    )
