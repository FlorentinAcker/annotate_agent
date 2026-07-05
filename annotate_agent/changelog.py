"""
Generate a comprehensive changelog documenting all modifications made.
Includes success/failure status, compilation status, and tokens used.
"""

from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime


def generate_changelog(
    project_root: Path,
    modifications: List[Dict[str, str]],
    results: List[Dict[str, Any]],
    compile_success: bool,
    compile_log: str,
    tokens_used: int
) -> Path:
    """
    Generate a CHANGELOG.md file documenting all changes.
    
    Args:
        project_root: Root directory of the project
        modifications: Original list of modifications requested by Claude
        results: Results of applying modifications
        compile_success: Whether LaTeX compilation succeeded
        compile_log: Full LaTeX compilation log
        tokens_used: Total API tokens consumed
    
    Returns:
        Path to generated changelog file
    """
    
    changelog_path = project_root / "CHANGELOG.md"
    
    # Categorize results
    successful = [r for r in results if r.get("success", False)]
    failed = [r for r in results if not r.get("success", False)]
    
    # Build markdown content
    lines = []
    
    # Header
    lines.append("# Annotation Processing Changelog\n")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**Timestamp:** {datetime.now().isoformat()}\n\n")
    
    # Summary stats
    lines.append("## Summary\n")
    lines.append(f"- ✅ **Successful modifications:** {len(successful)}/{len(results)}\n")
    if failed:
        lines.append(f"- ❌ **Failed modifications:** {len(failed)}/{len(results)}\n")
    lines.append(f"- 📊 **Compilation:** {'✅ Success' if compile_success else '❌ Failed'}\n")
    lines.append(f"- 🔤 **Tokens used:** {tokens_used}\n\n")
    
    # Successful modifications
    if successful:
        lines.append("## ✅ Applied Modifications\n\n")
        for idx, result in enumerate(successful, 1):
            lines.append(f"### {idx}. {result['file']}\n\n")
            
            annotation = result.get("annotation", "N/A")
            lines.append(f"**Annotation:**\n> {annotation}\n\n")
            
            explanation = result.get("explanation", "N/A")
            lines.append(f"**Change:**\n{explanation}\n\n")
    
    # Failed modifications
    if failed:
        lines.append("## ❌ Failed Modifications\n\n")
        for idx, result in enumerate(failed, 1):
            file_name = result.get("file", "unknown")
            error = result.get("error", "Unknown error")
            lines.append(f"### {idx}. {file_name}\n\n")
            lines.append(f"**Error:**\n```\n{error}\n```\n\n")
    
    # Compilation status
    lines.append("## 📊 Compilation Status\n\n")
    if compile_success:
        lines.append("✅ **Success**\n\n")
        lines.append("LaTeX compilation completed successfully.\n")
        lines.append("The PDF has been generated at `main.pdf`.\n\n")
    else:
        lines.append("❌ **Failed**\n\n")
        lines.append("⚠️ **Action required:** Fix the LaTeX errors below and recompile.\n\n")
        lines.append("### Compilation Log (last 3000 characters)\n\n")
        lines.append("```\n")
        # Show last 3000 chars to keep it readable
        log_snippet = compile_log[-3000:] if len(compile_log) > 3000 else compile_log
        lines.append(log_snippet)
        lines.append("\n```\n\n")
    
    # Write file
    changelog_content = "".join(lines)
    try:
        changelog_path.write_text(changelog_content, encoding="utf-8")
        return changelog_path
    except Exception as e:
        # If we can't write changelog, still return the path but log error
        print(f"Warning: Could not write changelog: {e}")
        return changelog_path