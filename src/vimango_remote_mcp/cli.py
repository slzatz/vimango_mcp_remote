"""CLI tool for vimango notes system."""

import sys
from pathlib import Path

import click

from .db import VimangoDatabase, load_config


@click.group()
@click.option(
    "--config",
    "config_path",
    default=str(Path(__file__).parent.parent.parent / "config.json"),
    help="Path to config.json",
)
@click.pass_context
def cli(ctx, config_path):
    """Vimango notes CLI - manage notes via the command line."""
    config = load_config(config_path)
    pg = config["postgres"]
    db = VimangoDatabase(
        host=pg["host"],
        port=pg["port"],
        user=pg["user"],
        password=pg["password"],
        dbname=pg["db"],
        ssl_mode=pg.get("ssl_mode", "disable"),
        ssl_ca_cert=pg.get("ssl_ca_cert"),
    )
    db.connect()
    ctx.obj = db
    ctx.call_on_close(db.close)


@cli.command()
@click.option("--title", required=True, help="Note title")
@click.option("--note", required=True, help="Note body (markdown)")
@click.option("--context", "context_name", default="none", help="Context name")
@click.option("--folder", "folder_name", default="none", help="Folder name")
@click.option("--star", is_flag=True, default=False, help="Star/favorite the note")
@click.pass_obj
def create(db, title, note, context_name, folder_name, star):
    """Create a new note."""
    context_result = db.get_context_by_name(context_name)
    if context_result is None:
        click.echo(f"Error: Context '{context_name}' not found.", err=True)
        sys.exit(1)
    context_tid, context_uuid = context_result

    folder_result = db.get_folder_by_name(folder_name)
    if folder_result is None:
        click.echo(f"Error: Folder '{folder_name}' not found.", err=True)
        sys.exit(1)
    folder_tid, folder_uuid = folder_result

    task_tid = db.insert_note(
        title=title,
        note=note,
        context_tid=context_tid,
        context_uuid=context_uuid,
        folder_tid=folder_tid,
        folder_uuid=folder_uuid,
        star=star,
    )
    click.echo(
        f"Created note '{title}' with tid {task_tid} "
        f"in folder '{folder_name}' and context '{context_name}'"
    )


@cli.command()
@click.pass_obj
def contexts(db):
    """List all available contexts."""
    rows = db.list_contexts()
    click.echo("Available contexts:")
    for tid, title, uuid, star in rows:
        star_marker = " *" if star else ""
        click.echo(f"- {title}{star_marker} (tid: {tid}, uuid: {uuid})")


@cli.command()
@click.pass_obj
def folders(db):
    """List all available folders."""
    rows = db.list_folders()
    click.echo("Available folders:")
    for tid, title, uuid, star in rows:
        star_marker = " *" if star else ""
        click.echo(f"- {title}{star_marker} (tid: {tid}, uuid: {uuid})")


@cli.command()
@click.argument("query")
@click.option("--limit", default=5, type=int, help="Maximum results to return")
@click.pass_obj
def search(db, query, limit):
    """Search notes using full-text search."""
    try:
        matches = db.find_notes(query, limit)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not matches:
        click.echo(f"No notes matched '{query}'.")
        sys.exit(1)

    click.echo(f"Matches for '{query}':")
    for match in matches:
        context_title = match.get("context_title") or "none"
        folder_title = match.get("folder_title") or "none"
        click.echo(
            f"{match['rank']}. {match['title']} "
            f"(context: {context_title}, folder: {folder_title}, "
            f"tid: {match['tid']})"
        )


@cli.command()
@click.option("--limit", default=5, type=int, help="Number of recent notes to show")
@click.pass_obj
def recent(db, limit):
    """Show the most recently modified notes."""
    notes = db.recent_notes(limit)
    if not notes:
        click.echo("No notes found.")
        sys.exit(1)

    click.echo(f"Recent notes (last {len(notes)}):")
    for note in notes:
        modified = note["modified"].strftime("%Y-%m-%d %H:%M") if note["modified"] else ""
        click.echo(
            f"  {note['title']} "
            f"(context: {note['context_title']}, folder: {note['folder_title']}, "
            f"tid: {note['tid']}, modified: {modified})"
        )


@cli.command()
@click.argument("tid", type=int)
@click.pass_obj
def get(db, tid):
    """Retrieve a note by its tid."""
    note_record = db.get_note_by_tid(tid)
    if not note_record:
        click.echo(f"No active note found with tid {tid}.", err=True)
        sys.exit(1)

    context_title = note_record.get("context_title") or "none"
    folder_title = note_record.get("folder_title") or "none"
    click.echo(f"Title: {note_record['title']}")
    click.echo(f"Context: {context_title}")
    click.echo(f"Folder: {folder_title}")
    click.echo(f"tid: {note_record['tid']}")
    body = note_record.get("note", "")
    if body:
        click.echo(f"\n{body}")


@cli.command()
@click.argument("tid", type=int)
@click.option("--title", default=None, help="New title")
@click.option("--note", default=None, help="New note body (replaces entire body)")
@click.option("--note-file", type=click.Path(exists=True), default=None, help="Read note body from file")
@click.option("--append", is_flag=True, default=False, help="Append to existing note body instead of replacing")
@click.option("--context", "context_name", default=None, help="New context name")
@click.option("--folder", "folder_name", default=None, help="New folder name")
@click.option("--star/--no-star", default=None, help="Star or unstar the note")
@click.pass_obj
def update(db, tid, title, note, note_file, append, context_name, folder_name, star):
    """Update an existing note's content or metadata."""
    if note is not None and note_file is not None:
        click.echo("Error: Cannot use both --note and --note-file.", err=True)
        sys.exit(1)

    if note_file is not None:
        with open(note_file) as f:
            note = f.read()

    if append and note is None:
        click.echo("Error: --append requires --note or --note-file.", err=True)
        sys.exit(1)

    if append and note is not None:
        existing = db.get_note_by_tid(tid)
        if existing is None:
            click.echo(f"No active note found with tid {tid}.", err=True)
            sys.exit(1)
        note = existing["note"] + "\n" + note

    if context_name is None and folder_name is None and title is None and star is None and note is None:
        click.echo("Error: At least one of --title, --note, --note-file, --context, --folder, --star/--no-star must be provided.", err=True)
        sys.exit(1)

    context_tid = context_uuid = None
    if context_name is not None:
        context_result = db.get_context_by_name(context_name)
        if context_result is None:
            click.echo(f"Error: Context '{context_name}' not found.", err=True)
            sys.exit(1)
        context_tid, context_uuid = context_result

    folder_tid = folder_uuid = None
    if folder_name is not None:
        folder_result = db.get_folder_by_name(folder_name)
        if folder_result is None:
            click.echo(f"Error: Folder '{folder_name}' not found.", err=True)
            sys.exit(1)
        folder_tid, folder_uuid = folder_result

    updated = db.update_note_metadata(
        tid=tid,
        context_tid=context_tid,
        context_uuid=context_uuid,
        folder_tid=folder_tid,
        folder_uuid=folder_uuid,
        title=title,
        star=star,
        note=note,
    )

    if updated:
        changes = []
        if context_name is not None:
            changes.append(f"context='{context_name}'")
        if folder_name is not None:
            changes.append(f"folder='{folder_name}'")
        if title is not None:
            changes.append(f"title='{title}'")
        if star is not None:
            changes.append(f"star={star}")
        if note is not None:
            changes.append("note updated" if not append else "note appended")
        click.echo(f"Updated note {tid}: {', '.join(changes)}")
    else:
        click.echo(f"No note found with tid {tid}, or no changes were made.", err=True)
        sys.exit(1)


def main():
    cli()
