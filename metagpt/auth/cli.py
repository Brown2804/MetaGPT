#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import typer

from metagpt.auth.store import AuthStore

app = typer.Typer(help="Manage MetaGPT auth profiles.", add_completion=False, pretty_exceptions_show_locals=False)


@app.command("status")
def status(provider: str = typer.Option(default="", help="Optional provider filter, e.g. openai-codex")):
    """Show locally stored auth profiles."""
    store = AuthStore.default()
    profiles = store.list_profiles(provider=provider or None)
    if not profiles:
        typer.echo("No auth profiles found.")
        return

    typer.echo(f"Auth store: {store.path}")
    for profile_id, profile in profiles.items():
        typer.echo(
            f"- {profile_id} | provider={profile.provider} | account_id={profile.account_id or '-'} | expires_at={profile.expires_at or '-'}"
        )


@app.command("import-codex")
def import_codex(
    profile: str = typer.Option(default="default", help="Target profile label."),
    auth_file: Path = typer.Option(default=Path.home() / ".codex" / "auth.json", help="Path to Codex CLI auth.json."),
    overwrite: bool = typer.Option(default=True, help="Overwrite if the target profile already exists."),
):
    """Import an existing Codex CLI auth cache into MetaGPT auth storage."""
    store = AuthStore.default()
    profile_id = store.import_codex_cli_auth(profile_label=profile, codex_auth_path=auth_file, overwrite=overwrite)
    typer.echo(f"Imported Codex auth into profile: {profile_id}")
    typer.echo(f"Auth store: {store.path}")


@app.command("login")
def login(
    provider: str = typer.Argument(..., help="Provider to log in, currently supports: codex"),
    profile: str = typer.Option(default="default", help="Target profile label."),
    auth_file: Path = typer.Option(default=Path.home() / ".codex" / "auth.json", help="Existing Codex CLI auth cache to import."),
):
    """Bootstrap login by reusing an existing Codex CLI OAuth cache."""
    normalized = provider.strip().lower()
    if normalized not in {"codex", "openai-codex", "openai_codex"}:
        raise typer.BadParameter("Only `codex` is supported right now.")

    store = AuthStore.default()
    profile_id = store.import_codex_cli_auth(profile_label=profile, codex_auth_path=auth_file, overwrite=True)
    typer.echo(f"Codex auth is ready in profile: {profile_id}")
    typer.echo("Full browser-based MetaGPT OAuth login flow is not implemented yet; this command currently reuses the local Codex CLI session.")


@app.command("logout")
def logout(profile_id: str = typer.Argument(..., help="Profile id, e.g. openai-codex:default")):
    """Delete a stored auth profile."""
    store = AuthStore.default()
    deleted = store.delete_profile(profile_id)
    if not deleted:
        raise typer.BadParameter(f"Profile not found: {profile_id}")
    typer.echo(f"Removed profile: {profile_id}")
