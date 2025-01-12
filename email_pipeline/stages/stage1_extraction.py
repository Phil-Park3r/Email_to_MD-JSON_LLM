"""
Stage 1: PST Extraction functionality.
"""

import os
import sys
import logging
import subprocess

def extract_pst(pst_path: str, output_dir: str) -> None:
    """Extract emails from PST file using readpst."""
    if not os.path.isfile(pst_path):
        logging.error(f"PST file not found: {pst_path}")
        raise FileNotFoundError(f"PST not found: {pst_path}")

    # Convert to absolute paths
    pst_path = os.path.abspath(pst_path)
    output_dir = os.path.abspath(output_dir)
    
    # Create directories
    os.makedirs(output_dir, exist_ok=True)
    attachments_dir = os.path.join(output_dir, 'attachments')
    os.makedirs(attachments_dir, exist_ok=True)

    # Store current directory
    original_dir = os.getcwd()
    
    try:
        # Change to output directory before running readpst
        os.chdir(output_dir)
        
        cmd = [
            'readpst', '-m', '-D', '-e',
            '-j', 'attachments',  # Now relative to output_dir
            '-o', '.',           # Current directory
            pst_path            # Absolute path to PST
        ]
        
        subprocess.run(cmd, check=True)
        logging.info(f"Raw PST extraction complete: {output_dir}")
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Error extracting PST file: {e.stderr if e.stderr else str(e)}")
        raise RuntimeError("readpst extraction failed") from e
    except Exception as e:
        logging.error(f"Error during PST extraction: {str(e)}")
        raise
    finally:
        # Always restore original directory
        os.chdir(original_dir) 