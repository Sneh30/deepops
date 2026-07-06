from setuptools import setup, find_packages

setup(
    name="deepops",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "deepops=deepops.__main__:main",
        ],
    },
    python_requires=">=3.10",
    author="DeepOps",
    description="Autonomous SRE Platform - AI-powered cloud operations",
)
