#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path

import typer

from metagpt.auth.cli import app as auth_app
from metagpt.const import CONFIG_ROOT

app = typer.Typer(add_completion=False, pretty_exceptions_show_locals=False)
app.add_typer(auth_app, name="auth")


def _parse_bool_option(value: str, option_name: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise typer.BadParameter(f"{option_name} must be one of: true/false, yes/no, 1/0")


def generate_repo(
    idea,
    investment=3.0,
    n_round=5,
    code_review=True,
    run_tests=False,
    implement=True,
    project_name="",
    inc=False,
    project_path="",
    reqa_file="",
    max_auto_summarize_code=0,
    recover_path=None,
):
    """Run the startup logic. Can be called from CLI or other Python scripts."""
    from metagpt.config2 import config
    from metagpt.context import Context
    from metagpt.roles import (
        Architect,
        DataAnalyst,
        Engineer2,
        ProductManager,
        TeamLeader,
    )
    from metagpt.team import Team

    config.update_via_cli(project_path, project_name, inc, reqa_file, max_auto_summarize_code)
    ctx = Context(config=config)

    if not recover_path:
        company = Team(context=ctx)
        company.hire(
            [
                TeamLeader(),
                ProductManager(),
                Architect(),
                Engineer2(),
                # ProjectManager(),
                DataAnalyst(),
            ]
        )

        # if implement or code_review:
        #     company.hire([Engineer(n_borg=5, use_code_review=code_review)])
        #
        # if run_tests:
        #     company.hire([QaEngineer()])
        #     if n_round < 8:
        #         n_round = 8  # If `--run-tests` is enabled, at least 8 rounds are required to run all QA actions.
    else:
        stg_path = Path(recover_path)
        if not stg_path.exists() or not str(stg_path).endswith("team"):
            raise FileNotFoundError(f"{recover_path} not exists or not endswith `team`")

        company = Team.deserialize(stg_path=stg_path, context=ctx)
        idea = company.idea

    company.invest(investment)
    asyncio.run(company.run(n_round=n_round, idea=idea))

    return ctx.kwargs.get("project_path")


@app.command("", help="Start a new project.")
def startup(
    idea: str = typer.Argument(None, help="Your innovative idea, such as 'Create a 2048 game.'"),
    investment: float = typer.Option(3.0, "--investment", help="Dollar amount to invest in the AI company."),
    n_round: int = typer.Option(5, "--n-round", help="Number of rounds for the simulation."),
    code_review: str = typer.Option("true", "--code-review", help="Whether to use code review. true/false."),
    run_tests: str = typer.Option("false", "--run-tests", help="Whether to enable QA for adding & running tests. true/false."),
    implement: str = typer.Option("true", "--implement", help="Enable or disable code implementation. true/false."),
    project_name: str = typer.Option("", "--project-name", help="Unique project name, such as 'game_2048'."),
    inc: str = typer.Option("false", "--inc", help="Incremental mode. true/false."),
    project_path: str = typer.Option(
        "",
        "--project-path",
        help="Specify the directory path of the old version project to fulfill the incremental requirements.",
    ),
    reqa_file: str = typer.Option(
        "", "--reqa-file", help="Specify the source file name for rewriting the quality assurance code."
    ),
    max_auto_summarize_code: int = typer.Option(
        0,
        "--max-auto-summarize-code",
        help="The maximum number of times the 'SummarizeCode' action is automatically invoked, with -1 indicating "
        "unlimited. This parameter is used for debugging the workflow.",
    ),
    recover_path: str = typer.Option(None, "--recover-path", help="recover the project from existing serialized storage"),
    init_config: str = typer.Option("false", "--init-config", help="Initialize the configuration file for MetaGPT. true/false."),
):
    """Run a startup. Be a boss."""
    init_config_bool = _parse_bool_option(init_config, "init_config")
    code_review_bool = _parse_bool_option(code_review, "code_review")
    run_tests_bool = _parse_bool_option(run_tests, "run_tests")
    implement_bool = _parse_bool_option(implement, "implement")
    inc_bool = _parse_bool_option(inc, "inc")

    if init_config_bool:
        copy_config_to()
        return

    if idea is None:
        typer.echo("Missing argument 'IDEA'. Run 'metagpt --help' for more information.")
        raise typer.Exit()

    return generate_repo(
        idea,
        investment,
        n_round,
        code_review_bool,
        run_tests_bool,
        implement_bool,
        project_name,
        inc_bool,
        project_path,
        reqa_file,
        max_auto_summarize_code,
        recover_path,
    )


DEFAULT_CONFIG = """# Full Example: https://github.com/geekan/MetaGPT/blob/main/config/config2.example.yaml
# Reflected Code: https://github.com/geekan/MetaGPT/blob/main/metagpt/config2.py
# Config Docs: https://docs.deepwisdom.ai/main/en/guide/get_started/configuration.html
llm:
  api_type: "openai"  # or azure / ollama / groq etc.
  model: "gpt-4-turbo"  # or gpt-3.5-turbo
  base_url: "https://api.openai.com/v1"  # or forward url / other llm url
  api_key: "YOUR_API_KEY"

# Experimental OAuth profile example for OpenAI-compatible Codex bridges:
# llm:
#   api_type: "openai_codex"
#   auth_mode: "oauth_profile"
#   auth_profile: "openai-codex:default"
#   base_url: "http://127.0.0.1:8787/v1"
#   api_key: ""
#   model: "gpt-5.4"
"""


def copy_config_to():
    """Initialize the configuration file for MetaGPT."""
    target_path = CONFIG_ROOT / "config2.yaml"

    # 创建目标目录（如果不存在）
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 如果目标文件已经存在，则重命名为 .bak
    if target_path.exists():
        backup_path = target_path.with_suffix(".bak")
        target_path.rename(backup_path)
        print(f"Existing configuration file backed up at {backup_path}")

    # 复制文件
    target_path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    print(f"Configuration file initialized at {target_path}")


if __name__ == "__main__":
    app()
