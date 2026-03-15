from setuptools import setup, find_packages

setup(
    name="llm-judge",
    version="0.1.0",
    description="Compare LLM responses side-by-side in your terminal",
    author="Gladiator Team",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    install_requires=[
        "httpx>=0.27.0",
        "rich>=13.0.0",
    ],
    entry_points={
        "console_scripts": [
            "llm-judge=llm_judge.cli:main",
        ],
    },
)
