"""Utilidades compartidas por los módulos de la CLI."""
import json

import typer

JSON = typer.Option(False, "--json", help="Salida JSON para máquinas/Claude")


def emit(rows, as_json: bool, cols: list[str] | None = None) -> None:
    rows = [dict(r) for r in rows]
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("(vacío)")
        return
    cols = cols or list(rows[0].keys())
    for r in rows:
        typer.echo("  ".join(str(r.get(c, "") if r.get(c) is not None else "") for c in cols))
