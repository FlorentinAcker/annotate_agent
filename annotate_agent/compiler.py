"""
Compile LaTeX documents using lualatex.
Handles errors gracefully and returns meaningful logs.
"""

import subprocess
import os
from pathlib import Path
from typing import Tuple


def compile_latex(project_root: Path) -> Tuple[bool, str]:
    """
    Compile the LaTeX document using lualatex.
    
    Runs lualatex twice (for references and bibliography).
    
    Args:
        project_root: Root directory of the project
    
    Returns:
        Tuple of (success: bool, log: str)
        - success: True if compilation succeeded
        - log: Full output from lualatex (stdout + stderr)
    """
    
    original_cwd = os.getcwd()
    
    try:
        os.chdir(project_root)
        
        # Check that lualatex is available
        try:
            subprocess.run(
                ["which", "lualatex"],
                capture_output=True,
                check=True,
                timeout=5
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False, "lualatex not found. Install with: sudo apt install texlive-full"
        
        # Run lualatex twice
        log_output = ""
        commands = [
            ["lualatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
            ["lualatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
        ]
        
        for pass_num, cmd in enumerate(commands, 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                log_output += result.stdout
                if result.stderr:
                    log_output += "\n--- STDERR ---\n" + result.stderr
                
                # Check return code
                if result.returncode != 0:
                    return False, log_output
            
            except subprocess.TimeoutExpired:
                return False, f"LaTeX compilation timed out on pass {pass_num}"
            except Exception as e:
                return False, f"LaTeX compilation error on pass {pass_num}: {e}"
        
        return True, log_output
    
    finally:
        os.chdir(original_cwd)