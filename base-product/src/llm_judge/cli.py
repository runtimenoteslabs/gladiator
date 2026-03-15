"""CLI entry point for llm-judge."""
import argparse
import sys
import time

from llm_judge.judge import compare_models
from llm_judge.display import print_results


def main():
    parser = argparse.ArgumentParser(
        prog="llm-judge",
        description="Compare LLM responses side-by-side in your terminal",
    )
    parser.add_argument("prompt", help="The prompt to send to all models")
    parser.add_argument(
        "--models",
        "-m",
        nargs="+",
        default=["claude-haiku", "claude-sonnet"],
        help="Models to compare (default: claude-haiku claude-sonnet)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=256,
        help="Max tokens per response (default: 256)",
    )
    parser.add_argument(
        "--temperature",
        "-t",
        type=float,
        default=0.7,
        help="Temperature (default: 0.7)",
    )

    args = parser.parse_args()

    results = compare_models(
        prompt=args.prompt,
        models=args.models,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )

    print_results(args.prompt, results)


if __name__ == "__main__":
    main()
