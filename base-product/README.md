# llm-judge

Compare LLM responses side-by-side in your terminal.

## Install

```bash
pip install -e .
```

## Usage

```bash
export ANTHROPIC_API_KEY=your-key

llm-judge "Explain quantum computing in one paragraph"
llm-judge "Write a haiku about coding" --models claude-haiku claude-sonnet
llm-judge "Optimize this SQL query" --max-tokens 512 --temperature 0.3
```

## Features

- Side-by-side response comparison
- Latency and token usage tracking
- Support for multiple Claude models
- Beautiful terminal output via Rich
