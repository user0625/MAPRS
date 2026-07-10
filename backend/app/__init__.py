"""
  cli: 一条命令从pdf生成markdown报告
    - uv run python -m app.cli \
        --pdf data/raw/example.pdf \
        --output outputs/reports/example_report.md \
        --query "Analyze this paper and generate a structured reading report." \
        --language zh
"""