#!/usr/bin/env python3
"""
Main entry point for annotate-agent CLI.
Orchestrates: detect structure → load files → extract annotations → apply → compile → changelog
"""

import os
import sys
import base64
import json
from pathlib import Path
from datetime import datetime
import click
from anthropic import Anthropic


def validate_structure(project_root: Path) -> dict:
    """
    Validate and detect project structure.
    
    Required:
    - main.tex
    - sections/ (with .tex files)
    
    Optional:
    - preamble.tex
    - references.bib
    - appendix/ (detected from PDF, validated here)
    
    Returns:
        dict with validated structure
    
    Raises:
        SystemExit if structure is invalid
    """
    structure = {
        "root": project_root,
        "main_file": None,
        "preamble_file": None,
        "bibfile": None,
        "sections": [],
        "appendix": [],
    }
    
    # Check main.tex
    main_file = project_root / "main.tex"
    if not main_file.exists():
        click.echo("❌ main.tex not found in project root", err=True)
        sys.exit(1)
    structure["main_file"] = main_file
    
    # Check sections/
    sections_dir = project_root / "sections"
    if not sections_dir.exists():
        click.echo("❌ sections/ directory not found", err=True)
        sys.exit(1)
    
    section_files = sorted(sections_dir.glob("*.tex"))
    if not section_files:
        click.echo("❌ sections/ directory is empty", err=True)
        sys.exit(1)
    structure["sections"] = section_files
    
    # Check optional files
    preamble_file = project_root / "preamble.tex"
    if preamble_file.exists():
        structure["preamble_file"] = preamble_file
    else:
        click.echo("⚠️  preamble.tex not found (optional)", err=False)
    
    bibfile = project_root / "references.bib"
    if bibfile.exists():
        structure["bibfile"] = bibfile
    else:
        click.echo("⚠️  references.bib not found (optional)", err=False)
    
    # appendix/ will be checked after Claude reads PDF
    appendix_dir = project_root / "appendix"
    if appendix_dir.exists():
        appendix_files = sorted(appendix_dir.glob("*.tex"))
        if appendix_files:
            structure["appendix"] = appendix_files
    
    return structure


def load_tex_files(structure: dict) -> dict:
    """Load all .tex files into memory with proper labels."""
    tex_files = {}
    
    # Load in order: preamble → sections → appendix → main
    if structure["preamble_file"]:
        try:
            content = structure["preamble_file"].read_text(encoding="utf-8")
            tex_files["preamble.tex"] = content
        except Exception as e:
            click.echo(f"❌ Error reading preamble.tex: {e}", err=True)
            sys.exit(1)
    
    # Sections
    for section_file in structure["sections"]:
        try:
            content = section_file.read_text(encoding="utf-8")
            rel_path = f"sections/{section_file.name}"
            tex_files[rel_path] = content
        except Exception as e:
            click.echo(f"❌ Error reading {section_file}: {e}", err=True)
            sys.exit(1)
    
    # Appendix
    for appendix_file in structure["appendix"]:
        try:
            content = appendix_file.read_text(encoding="utf-8")
            rel_path = f"appendix/{appendix_file.name}"
            tex_files[rel_path] = content
        except Exception as e:
            click.echo(f"❌ Error reading {appendix_file}: {e}", err=True)
            sys.exit(1)
    
    # Main (last)
    try:
        content = structure["main_file"].read_text(encoding="utf-8")
        tex_files["main.tex"] = content
    except Exception as e:
        click.echo(f"❌ Error reading main.tex: {e}", err=True)
        sys.exit(1)
    
    return tex_files


def read_pdf_as_base64(pdf_path: Path) -> str:
    """Read PDF and encode as base64."""
    try:
        with open(pdf_path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        click.echo(f"❌ PDF file not found: {pdf_path}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Error reading PDF: {e}", err=True)
        sys.exit(1)


def build_context_for_claude(tex_files: dict) -> str:
    """Build LaTeX context string for Claude."""
    context = "# LaTeX Source Files\n\n"
    for filename in sorted(tex_files.keys()):
        context += f"\n## File: {filename}\n```latex\n{tex_files[filename]}\n```\n"
    return context


def extract_annotations_from_pdf(
    client: Anthropic, 
    pdf_base64: str, 
    tex_context: str
) -> tuple[list, int, int]:
    """
    Call Claude to extract annotations and propose modifications.
    
    Returns:
        (modifications_list, input_tokens, output_tokens)
    
    Raises:
        SystemExit on failure
    """
    
    prompt = f"""You are analyzing an annotated LaTeX research paper.

**Your task:**
1. Extract ALL annotations from the PDF (yellow highlights + red comments)
2. For each annotation, find the EXACT corresponding text in the LaTeX sources
3. Understand what modification the comment requests
4. Propose the exact replacement text

**Important:**
- Return ONLY valid JSON, nothing else
- No markdown backticks, no preamble, no explanation
- Each modification must have all 4 fields

**JSON Format (example):**
[
  {{
    "file": "main.tex",
    "annotation": "the red comment text",
    "old_text": "exact text from LaTeX to replace",
    "new_text": "the proposed replacement",
    "explanation": "why this change makes sense"
  }}
]

**CRITICAL:**
- The old_text MUST be found exactly in the LaTeX files
- The new_text should follow the comment's instruction precisely
- If annotation is ambiguous, ask for clarification in the error

# LaTeX Sources

{tex_context}"""
    
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[
                {{
                    "role": "user",
                    "content": [
                        {{"type": "text", "text": prompt}},
                        {{
                            "type": "document",
                            "source": {{
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_base64
                            }}
                        }}
                    ]
                }}
            ]
        )
    except Exception as e:
        click.echo(f"❌ API call failed: {e}", err=True)
        sys.exit(1)
    
    # Parse response
    response_text = message.content[0].text.strip()
    
    # Clean markdown backticks if present
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    
    response_text = response_text.strip()
    
    # Parse JSON
    try:
        modifications = json.loads(response_text)
        
        # Validate structure
        if not isinstance(modifications, list):
            click.echo("❌ Claude returned non-list JSON", err=True)
            sys.exit(1)
        
        for mod in modifications:
            required_keys = {"file", "annotation", "old_text", "new_text", "explanation"}
            if not required_keys.issubset(mod.keys()):
                missing = required_keys - set(mod.keys())
                click.echo(f"❌ Missing keys in modification: {missing}", err=True)
                sys.exit(1)
        
        return modifications, message.usage.input_tokens, message.usage.output_tokens
    
    except json.JSONDecodeError as e:
        click.echo(f"❌ Failed to parse Claude's response as JSON", err=True)
        click.echo(f"Error: {e}", err=True)
        click.echo(f"\nResponse was:\n{response_text[:500]}", err=True)
        sys.exit(1)


def apply_modifications_to_files(
    structure: dict, 
    modifications: list
) -> list:
    """
    Apply modifications to LaTeX files.
    
    Returns:
        list of application results (success/failure per mod)
    """
    from .applier import apply_modifications
    return apply_modifications(structure, modifications)


def compile_project(project_root: Path) -> tuple[bool, str]:
    """Compile LaTeX project."""
    from .compiler import compile_latex
    return compile_latex(project_root)


def generate_changelog_file(
    project_root: Path,
    modifications: list,
    results: list,
    compile_success: bool,
    compile_log: str,
    tokens_used: int
) -> Path:
    """Generate changelog."""
    from .changelog import generate_changelog
    return generate_changelog(
        project_root,
        modifications,
        results,
        compile_success,
        compile_log,
        tokens_used
    )


@click.command()
@click.argument("pdf_file", type=click.Path(exists=True))
def cli(pdf_file: str):
    """
    Process annotations in a LaTeX paper PDF and apply modifications.
    
    Run from the root of your LaTeX project.
    The PDF file should be in the project root and contain your annotations.
    """
    
    pdf_path = Path(pdf_file).resolve()
    project_root = Path.cwd()
    
    click.echo("=" * 60)
    click.echo("📋 Annotate Agent - Processing PDF annotations")
    click.echo("=" * 60)
    
    # Validate structure
    click.echo("\n🔍 Validating project structure...")
    try:
        structure = validate_structure(project_root)
        click.echo(f"✅ Structure valid")
        click.echo(f"   - main.tex found")
        click.echo(f"   - {len(structure['sections'])} section(s) found")
        if structure["appendix"]:
            click.echo(f"   - {len(structure['appendix'])} appendix file(s) found")
    except SystemExit:
        raise
    
    # Load files
    click.echo("\n📖 Loading LaTeX sources...")
    tex_files = load_tex_files(structure)
    click.echo(f"✅ Loaded {len(tex_files)} file(s)")
    
    # Read PDF
    click.echo("\n📄 Reading PDF...")
    pdf_base64 = read_pdf_as_base64(pdf_path)
    click.echo("✅ PDF loaded")
    
    # Extract annotations
    click.echo("\n🤖 Extracting annotations with Claude...")
    client = Anthropic()
    tex_context = build_context_for_claude(tex_files)
    modifications, input_tokens, output_tokens = extract_annotations_from_pdf(
        client, pdf_base64, tex_context
    )
    
    if not modifications:
        click.echo("⚠️  No annotations found")
        return
    
    click.echo(f"✅ Found {len(modifications)} annotation(s)")
    
    # Apply modifications
    click.echo("\n✏️  Applying modifications...")
    results = apply_modifications_to_files(structure, modifications)
    
    successful = [r for r in results if r.get("success", False)]
    failed = [r for r in results if not r.get("success", False)]
    
    click.echo(f"✅ Applied {len(successful)} modification(s)")
    if failed:
        click.echo(f"⚠️  {len(failed)} modification(s) failed")
    
    # Compile
    click.echo("\n🔨 Compiling LaTeX...")
    compile_success, compile_log = compile_project(project_root)
    
    if compile_success:
        click.echo("✅ Compilation successful")
    else:
        click.echo("❌ Compilation failed (see CHANGELOG.md for details)")
    
    # Generate changelog
    click.echo("\n📝 Generating changelog...")
    changelog_path = generate_changelog_file(
        project_root,
        modifications,
        results,
        compile_success,
        compile_log,
        input_tokens + output_tokens
    )
    click.echo(f"✅ Changelog generated: {changelog_path}")
    
    # Summary
    click.echo("\n" + "=" * 60)
    click.echo("✅ Process complete!")
    click.echo("=" * 60)
    click.echo(f"📋 Changelog: {changelog_path}")
    if compile_success:
        click.echo(f"📊 PDF: {project_root / 'main.pdf'}")
    click.echo(f"📈 Tokens used: {input_tokens + output_tokens}")
    click.echo("=" * 60 + "\n")


if __name__ == "__main__":
    cli()