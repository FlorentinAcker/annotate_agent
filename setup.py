from setuptools import setup, find_packages

setup(
    name="annotate-agent",
    version="0.1.0",
    description="AI-powered annotation processor for LaTeX papers",
    author="Florentin Acker",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "anthropic>=0.7.0",
        "pdfplumber>=0.10.0",
        "click>=8.0.0",
    ],
    entry_points={
        "console_scripts": [
            "annotate-paper=annotate_agent.main:cli",
        ],
    },
)