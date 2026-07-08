from backend.schemas.report import FinalReport, ReportFormat, ReportSection


def test_report_section_to_markdown():
    section = ReportSection(
        title="Method Summary",
        content="The method uses multiple specialized agents.",
        order=2,
        evidence_ids=["ev_001", "ev_002"],
    )

    markdown = section.to_markdown()

    assert "## Method Summary" in markdown
    assert "multiple specialized agents" in markdown
    assert "ev_001" in markdown


def test_final_report_to_markdown_from_sections():
    report = FinalReport(
        title="Paper Reading Report",
        paper_title="Example Paper",
        sections=[
            ReportSection(
                title="Overall Assessment",
                content="The paper is useful but has limited evaluation.",
                order=3,
            ),
            ReportSection(
                title="TL;DR",
                content="This paper proposes a multi-agent paper reader.",
                order=1,
            ),
            ReportSection(
                title="Method Summary",
                content="The system contains Planner, Reader, Critic, and Writer agents.",
                order=2,
            ),
        ],
    )

    markdown = report.to_markdown()

    assert markdown.startswith("# Paper Reading Report")
    assert "**Paper:** Example Paper" in markdown

    tldr_index = markdown.index("## TL;DR")
    method_index = markdown.index("## Method Summary")
    assessment_index = markdown.index("## Overall Assessment")

    assert tldr_index < method_index < assessment_index


def test_final_report_uses_cached_markdown_content():
    report = FinalReport(
        title="Paper Reading Report",
        markdown_content="# Custom Report\n\nThis is cached content.",
    )

    markdown = report.to_markdown()

    assert markdown == "# Custom Report\n\nThis is cached content."


def test_report_format_enum():
    report = FinalReport(format=ReportFormat.MARKDOWN)

    assert report.format == ReportFormat.MARKDOWN