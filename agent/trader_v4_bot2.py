#!/usr/bin/env python3
"""Compatibility launcher for the modular Max Alpha v4 trading agent."""

try:
    from .bot import MaxAlphaV4, check, startup_prompt
except ImportError:
    from bot import MaxAlphaV4, check, startup_prompt


def main():
    check()
    run_mode, budget = startup_prompt()
    MaxAlphaV4(budget=budget, run_mode=run_mode).run()


if __name__ == "__main__":
    main()
