#!/usr/bin/env python3
"""
Main entry point for annotate-agent CLI.
Orchestrates: detect structure -> load files -> extract annotations -> apply -> compile -> changelog
"""

import os
import sys
import base64
import json
from pathlib import Path
import click
from anthropic import Anthropic

from .applier import apply_modifications
from .compiler import compile_latex
from .changelog import generate_changelog


# ──────────────────────────────────────────────
# Project structure
# ──────────────────────────────────────────────

def validate_structure(project_root: Path) -> dict:
    """
    Validate and detect project structure.

    Required:
      - main.tex
      - sections/ (with at least one .tex)

    Optional:
      - preamble.tex
      - references.bib
      - appendix/ (validated after Claude reads PDF)
    """
    structure = {
        "root":          project_root,
        "main_file":     None,
        "preamble_file": None,
        "bibfile":       None,
        "sections":      [],
        "appendix":      [],
    }

    # main.tex - required
    main_file = project_root / "main.tex"
    if not main_file.exists():
        click.echo("[ERROR] main.tex not found in project root", err=True)
        sys.exit(1)
    structure["main_file"] = main_file

    # sections/ - required, must contain .tex files
    sections_dir = project_root / "sections"
    if not sections_dir.exists():
        click.echo("[ERROR] sections/ directory not found", err=True)
        sys.exit(1)
    section_files = sorted(sections_dir.glob("*.tex"))
    if not section_files:
        click.echo("[ERROR] sections/ directory is empty", err=True)
        sys.exit(1)
    structure["sections"] = section_files

    # preamble.tex - optional
    preamble_file = project_root / "preamble.tex"
    if preamble_file.exists():
        structure["preamble_file"] = preamble_file
    else:
        click.echo("[WARNING] preamble.tex not found (optional, skipping)")

    # references.bib - optional
    bibfile = project_root / "references.bib"
    if bibfile.exists():
        structure["bibfile"] = bibfile
    else:
        click.echo("[WARNING] references.bib not found (optional, skipping)")

    # appendix/ - optional, validated after Claude reads PDF
    appendix_dir = project_root / "appendix"
    if appendix_dir.exists():
        appendix_files = sorted(appendix_dir.glob("*.tex"))
        if appendix_files:
            structure["appendix"] = appendix_files

    return structure


def validate_appendix(structure: dict, has_appendix_in_pdf: bool) -> None:
    """
    Cross-check appendix/ on disk with what Claude found in the PDF.
    Raises SystemExit on mismatch.
    """
    has_appendix_on_disk = bool(structure["appendix"])

    if has_appendix_in_pdf and not has_appendix_on_disk:
        click.echo(
            "[ERROR] PDF contains an appendix but appendix/ directory is missing or empty",
            err=True,
        )
        sys.exit(1)

    if not has_appendix_in_pdf and has_appendix_on_disk:
        click.echo(
            "[ERROR] appendix/ directory found on disk but PDF has no appendix",
            err=True,
        )
        sys.exit(1)


# ──────────────────────────────────────────────
# File loading
# ──────────────────────────────────────────────

def load_tex_files(structure: dict) -> dict:
    """
    Load all .tex files into memory.

    Order: preamble.tex -> sections/* -> appendix/* -> main.tex
    """
    tex_files = {}

    def _read(label: str, path: Path) -> None:
        try:
            tex_files[label] = path.read_text(encoding="utf-8")
        except Exception as exc:
            click.echo(f"[ERROR] Error reading {label}: {exc}", err=True)
            sys.exit(1)

    if structure["preamble_file"]:
        _read("preamble.tex", structure["preamble_file"])

    for f in structure["sections"]:
        _read(f"sections/{f.name}", f)

    for f in structure["appendix"]:
        _read(f"appendix/{f.name}", f)

    _read("main.tex", structure["main_file"])

    return tex_files


# ──────────────────────────────────────────────
# PDF loading
# ──────────────────────────────────────────────

def read_pdf_as_base64(pdf_path: Path) -> str:
    """Read PDF and return base64-encoded string (ASCII safe)."""
    try:
        raw_bytes = pdf_path.read_bytes()
        return base64.b64encode(raw_bytes).decode("ascii")
    except FileNotFoundError:
        click.echo(f"[ERROR] PDF not found: {pdf_path}", err=True)
        sys.exit(1)
    except Exception as exc:
        click.echo(f"[ERROR] Error reading PDF: {exc}", err=True)
        sys.exit(1)


# ──────────────────────────────────────────────
# Claude call
# ──────────────────────────────────────────────

JSON_EXAMPLE = """\
[
  {
    "file": "sections/section1.tex",
    "annotation": "the exact red comment text",
    "old_text": "exact LaTeX text to replace",
    "new_text": "proposed replacement text",
    "explanation": "why this change makes sense",
    "has_appendix": false
  }
]"""


def build_prompt(tex_files: dict) -> str:
    """Build the prompt sent to Claude alongside the PDF."""
    tex_context = "\n\n".join(
        f"## File: {name}\n```latex\n{content}\n```"
        for name, content in tex_files.items()
    )

    return f"""You are analyzing an annotated LaTeX research paper.

Your task:
1. Extract ALL annotations from the PDF (yellow highlights + red comments).
2. For each annotation, find the EXACT corresponding passage in the LaTeX sources.
3. Understand what the red comment asks for and propose the replacement text.
4. Set "has_appendix" to true if the PDF contains an appendix section, false otherwise.

Rules:
- Return ONLY valid JSON. No markdown fences, no prose, no preamble.
- Every object must have all 6 fields shown in the example.
- "old_text" must appear VERBATIM in the LaTeX file you name.
- "new_text" must implement the comment faithfully.

JSON format (example):
{JSON_EXAMPLE}

---

LaTeX sources:

{tex_context}"""


def call_claude(client: Anthropic, pdf_base64: str, tex_files: dict) -> tuple:
    """
    Send PDF + LaTeX sources to Claude.

    Returns:
        (modifications, has_appendix, input_tokens, output_tokens)
    """
    prompt = build_prompt(tex_files)

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_base64,
                            },
                        },
                    ],
                }
            ],
        )
    except Exception as exc:
        click.echo(f"[ERROR] API call failed: {exc}", err=True)
        sys.exit(1)

    raw = message.content[0].text.strip()

    # Strip markdown fences if Claude adds them despite the instruction
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    # Parse JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        click.echo("[ERROR] Claude returned invalid JSON", err=True)
        click.echo(f"  Error  : {exc}", err=True)
        click.echo(f"  Preview: {raw[:400]}", err=True)
        sys.exit(1)

    if not isinstance(data, list):
        click.echo("[ERROR] Claude JSON is not a list", err=True)
        sys.exit(1)

    # Validate required keys
    required = {"file", "annotation", "old_text", "new_text", "explanation"}
    for idx, mod in enumerate(data):
        missing = required - set(mod.keys())
        if missing:
            click.echo(f"[ERROR] Modification #{idx+1} is missing keys: {missing}", err=True)
            sys.exit(1)

    # Extract has_appendix from first item
    has_appendix = bool(data[0].get("has_appendix", False)) if data else False

    return data, has_appendix, message.usage.input_tokens, message.usage.output_tokens


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

@click.command()
@click.argument("pdf_file", type=click.Path(exists=True))
def cli(pdf_file: str):
    """
    Process annotations in a LaTeX paper PDF and apply modifications.

    Run from the root of your LaTeX project.
    The annotated PDF must be in the project root.
    """
    pdf_path     = Path(pdf_file).resolve()
    project_root = Path.cwd()

    click.echo("=" * 60)
    click.echo("Annotate Agent - Processing PDF annotations")
    click.echo("=" * 60)

    # 1. Validate structure
    click.echo("\n[1/6] Validating project structure...")
    structure = validate_structure(project_root)
    click.echo(f"  main.tex found")
    click.echo(f"  {len(structure['sections'])} section(s) found")
    if structure["appendix"]:
        click.echo(f"  {len(structure['appendix'])} appendix file(s) found")

    # 2. Load files
    click.echo("\n[2/6] Loading LaTeX sources...")
    tex_files = load_tex_files(structure)
    click.echo(f"  {len(tex_files)} file(s) loaded")

    # 3. Read PDF
    click.echo("\n[3/6] Reading annotated PDF...")
    pdf_base64 = read_pdf_as_base64(pdf_path)
    click.echo("  PDF loaded")

    # 4. Call Claude
    click.echo("\n[4/6] Extracting annotations with Claude...")
    client = Anthropic()
    modifications, has_appendix, input_tokens, output_tokens = call_claude(
        client, pdf_base64, tex_files
    )
    click.echo(f"  {len(modifications)} annotation(s) found")

    # 5. Validate appendix consistency
    validate_appendix(structure, has_appendix)

    # 6. Apply modifications
    click.echo("\n[5/6] Applying modifications...")
    results    = apply_modifications(structure, modifications)
    successful = [r for r in results if r.get("success")]
    failed     = [r for r in results if not r.get("success")]
    click.echo(f"  {len(successful)} applied")
    if failed:
        click.echo(f"  {len(failed)} failed")

    # 7. Compile
    click.echo("\n[6/6] Compiling with lualatex...")
    compile_success, compile_log = compile_latex(project_root)
    if compile_success:
        click.echo("  Compilation successful")
    else:
        click.echo("  Compilation failed (see CHANGELOG.md)")

    # 8. Changelog
    click.echo("\nGenerating changelog...")
    changelog_path = generate_changelog(
        project_root,
        modifications,
        results,
        compile_success,
        compile_log,
        input_tokens + output_tokens,
    )
    click.echo(f"  Changelog written to {changelog_path}")

    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("Done!")
    click.echo(f"  Changelog : {changelog_path}")
    if compile_success:
        click.echo(f"  PDF       : {project_root / 'main.pdf'}")
    click.echo(f"  Tokens    : {input_tokens + output_tokens}")
    click.echo("=" * 60 + "\n")


if __name__ == "__main__":
    cli()