"""
Apply modifications to LaTeX source files.
Performs find-and-replace operations with proper validation and error handling.
"""

from pathlib import Path
from typing import List, Dict, Any


def apply_modifications(structure: Dict[str, Any], modifications: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    Apply all modifications to the appropriate LaTeX files.
    
    For each modification:
    1. Find the correct file
    2. Verify old_text exists exactly once
    3. Replace with new_text
    4. Write to disk
    
    Args:
        structure: Project structure dict with file paths
        modifications: List of modifications from Claude
    
    Returns:
        List of results dicts with success/failure per modification
    """
    
    results = []
    
    # Build a map of relative paths to absolute paths
    file_map = {}
    
    # Add all files
    file_map["main.tex"] = structure["main_file"]
    
    for section_file in structure["sections"]:
        rel_path = f"sections/{section_file.name}"
        file_map[rel_path] = section_file
    
    for appendix_file in structure["appendix"]:
        rel_path = f"appendix/{appendix_file.name}"
        file_map[rel_path] = appendix_file
    
    if structure["preamble_file"]:
        file_map["preamble.tex"] = structure["preamble_file"]
    
    # Load all files into memory first
    file_contents = {}
    for rel_path, abs_path in file_map.items():
        try:
            file_contents[rel_path] = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            results.append({
                "file": rel_path,
                "success": False,
                "error": f"Failed to read file: {e}"
            })
            return results  # Stop on read error
    
    # Apply each modification
    for mod_idx, mod in enumerate(modifications):
        file_name = mod.get("file")
        old_text = mod.get("old_text")
        new_text = mod.get("new_text")
        annotation = mod.get("annotation", "")
        explanation = mod.get("explanation", "")
        
        # Validate we have required fields
        if not all([file_name, old_text, new_text]):
            results.append({
                "file": file_name or "unknown",
                "success": False,
                "error": "Missing required fields (file, old_text, new_text)"
            })
            continue
        
        # Check file exists in our map
        if file_name not in file_map:
            results.append({
                "file": file_name,
                "success": False,
                "error": f"File not found in project: {file_name}"
            })
            continue
        
        # Get current content
        current_content = file_contents[file_name]
        
        # Check if old_text exists
        if old_text not in current_content:
            results.append({
                "file": file_name,
                "success": False,
                "error": f"Text not found in file. Searched for: {old_text[:80]}..."
            })
            continue
        
        # Check for ambiguity (multiple occurrences)
        count = current_content.count(old_text)
        if count > 1:
            results.append({
                "file": file_name,
                "success": False,
                "error": f"Text appears {count} times in file (ambiguous). Cannot replace safely."
            })
            continue
        
        # Perform replacement
        try:
            new_content = current_content.replace(old_text, new_text)
            file_contents[file_name] = new_content
            
            results.append({
                "file": file_name,
                "success": True,
                "annotation": annotation,
                "explanation": explanation
            })
        except Exception as e:
            results.append({
                "file": file_name,
                "success": False,
                "error": f"Replacement failed: {e}"
            })
            continue
    
    # Write all modified files back to disk
    for file_name, content in file_contents.items():
        try:
            file_map[file_name].write_text(content, encoding="utf-8")
        except Exception as e:
            # Add error for this file
            for result in results:
                if result["file"] == file_name:
                    result["write_error"] = f"Failed to write file: {e}"
                    break
            return results  # Stop on write error
    
    return results