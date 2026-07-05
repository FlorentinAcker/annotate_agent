#!/usr/bin/env python3
"""
Main entry point for the annotate-agent CLI.
Orchestrates the workflow: extract annotations → apply modifications → compile → generate changelog.
"""

import os
import sys
import base64
import json
from pathlib import Path
import click
from anthropic import Anthropic

from .extractor import extract_annotations
from .applier import apply_modifications
from .compiler import compile_latex
from .changelog import generate_changelog


def load_project_structure(project_root: Path) -> dict:
    """Detect LaTeX project structure."""
    structure = {
        "main_file": project_root / "main.tex",
        "sections": [],
        "appendix": [],
        "bibfile": project_root / "references.bib",
        "root": project_root,
    }
    
    # Find sections
    sections_dir = project_root / "sections"
    if sections_dir.exists():
        structure["sections"] = sorted(sections_dir.glob("*.tex"))
    
    # Find appendix
    appendix_dir = project_root / "appendice"
    if appendix_dir.exists():
        structure["appendix"] = sorted(appendix_dir.glob("*.tex"))
    
    return structure


def read_all_tex_files(structure: dict) -> dict:
    """Read all .tex files into memory."""
    tex_files = {}
    
    if structure["main_file"].exists():
        tex_files["main.tex"] = structure["main_file"].read_text(encoding="utf-8")
    
    for section_file in structure["sections"]:
        rel_path = f"sections/{section_file.name}"
        tex_files[rel_path] = section_file.read_text(encoding="utf-8")
    
    for appendix_file in structure["appendix"]:
        rel_path = f"appendice/{appendix_file.name}"
        tex_files[rel_path] = appendix_file.read_text(encoding="utf-8")
    
    return tex_files


def read_pdf_as_base64(pdf_path: Path) -> str:
    """Read PDF file and encode as base64."""
    with open(pdf_path, "rb") as f:
        return base64.standard_b64encode(f.read()).decode("utf-8")


def extract_annotations_via_claude(client: Anthropic, pdf_base64: str, tex_files: dict) -> list:
    """
    Call Claude API to extract and understand annotations.
    Returns list of modifications to apply.
    """
    
    # Build context: all LaTeX files
    tex_context = "# LaTeX Source Files\n\n"
    for filename, content in tex_files.items():
        tex_context += f"\n## {filename}\n```latex\n{content}\n```\n"
    
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"""You are analyzing an annotated LaTeX paper. 
                        
Your task:
1. Extract all annotations (highlighted text + red comments) from the PDF
2. For each annotation, identify which section of the LaTeX source it corresponds to
3. Understand what modification is requested in the comment
4. Propose the exact change needed

Return a JSON array with this structure:
[
  {{
    "file": "main.tex or sections/xxx.tex or appendice/xxx.tex",
    "annotation": "the red comment text",
    "old_text": "exact text to find and replace",
    "new_text": "the proposed replacement",
    "explanation": "brief explanation of the change"
  }},
  ...
]

Only return valid JSON, nothing else.

# LaTeX Source Files

{tex_context}"""
                    },
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_base64
                        }
                    }
                ]
            }
        ]
    )
    
    # Parse the response
    response_text = message.content[0].text
    
    # Try to extract JSON
    try:
        # Find JSON array in response
        start_idx = response_text.find("[")
        end_idx = response_text.rfind("]") + 1
        if start_idx >= 0 and end_idx > start_idx:
            json_str = response_text[start_idx:end_idx]
            modifications = json.loads(json_str)
            return modifications, message.usage.input_tokens, message.usage.output_tokens
    except json.JSONDecodeError:
        click.echo("❌ Failed to parse Claude's response as JSON", err=True)
        click.echo(f"Response was:\n{response_text}", err=True)
        sys.exit(1)
    
    return [], 0, 0


@click.command()
@click.argument("pdf_file", type=click.Path(exists=True))
def cli(pdf_file: str):
    """
    Process annotations in a LaTeX paper PDF and apply modifications.
    
    Run from the root of your LaTeX project.
    The PDF file should be in the project root.
    """
    
    pdf_path = Path(pdf_file)
    project_root = Path.cwd()
    
    click.echo("🔍 Detecting project structure...")
    structure = load_project_structure(project_root)
    
    # Verify main.tex exists
    if not structure["main_file"].exists():
        click.echo("❌ main.tex not found in current directory", err=True)
        sys.exit(1)
    
    click.echo(f"✅ Found {len(structure['sections'])} sections, {len(structure['appendix'])} appendix files")
    
    # Read all LaTeX files
    click.echo("📖 Loading LaTeX sources...")
    tex_files = read_all_tex_files(structure)
    
    # Read PDF
    click.echo("📄 Reading annotated PDF...")
    pdf_base64 = read_pdf_as_base64(pdf_path)
    
    # Extract annotations via Claude
    click.echo("🤖 Extracting annotations with Claude...")
    client = Anthropic()
    modifications, input_tokens, output_tokens = extract_annotations_via_claude(client, pdf_base64, tex_files)
    
    if not modifications:
        click.echo("⚠️  No annotations found", err=False)
        return
    
    click.echo(f"✅ Found {len(modifications)} annotations")
    
    # Apply modifications
    click.echo("✏️  Applying modifications...")
    results = apply_modifications(structure, modifications)
    
    # Compile LaTeX
    click.echo("🔨 Compiling LaTeX...")
    compile_success, compile_log = compile_latex(project_root)
    
    # Generate changelog
    click.echo("📝 Generating changelog...")
    changelog_path = generate_changelog(
        project_root,
        modifications,
        results,
        compile_success,
        compile_log,
        input_tokens + output_tokens
    )
    
    # Summary
    click.echo("\n" + "="*50)
    click.echo("✅ Process complete!")
    click.echo(f"📋 Changelog: {changelog_path}")
    if compile_success:
        click.echo(f"📊 New PDF: {project_root / 'main.pdf'}")
    else:
        click.echo("⚠️  LaTeX compilation failed (see CHANGELOG.md for details)")
    click.echo(f"📊 Tokens used: {input_tokens + output_tokens}")
    click.echo("="*50)


if __name__ == "__main__":
    cli()