"""
Pipeline orchestration for email processing.
"""

import logging
from pathlib import Path
from typing import Optional

from email_pipeline.stages.stage1_extraction import extract_pst
from email_pipeline.stages.stage2_thread_analysis import analyze_threads
from email_pipeline.stages.stage3_content_processing import process_content
from email_pipeline.stages.stage4_optimization import tokenize_and_optimize
from email_pipeline.utils import secure_delete_directory
from email_pipeline.config import TARGET_USER_NAME

def cleanup_intermediate_files(
    stage1_output: str,
    stage3_output: str,
    keep_extracted: bool = False,
    keep_markdown: bool = False
) -> None:
    """Clean up intermediate files and directories."""
    if not keep_extracted:
        logging.info("Cleaning up extracted data...")
        secure_delete_directory(stage1_output)
    
    if not keep_markdown:
        logging.info("Cleaning up processed markdown...")
        secure_delete_directory(stage3_output)

def run_pipeline(
    pst_file: str,
    stage1_output: str,
    stage2_output: Optional[str],
    stage3_output: str,
    stage4_output: str,
    token_limit: int,
    keep_extracted: bool = False,
    keep_markdown: bool = False,
    user_name: str = TARGET_USER_NAME
):
    """Run the complete email processing pipeline."""
    try:
        logging.info("[Stage 1] Raw Extraction")
        extract_pst(pst_file, stage1_output)

        logging.info("[Stage 2] Thread Analysis")
        stage_two_dir = stage2_output if stage2_output is not None else stage1_output
        analyze_threads(stage_two_dir, user_name)

        logging.info("[Stage 3] Content Processing")
        eml_files = list(Path(stage1_output).rglob("*.eml"))
        process_content(eml_files, stage3_output, user_name)

        logging.info("[Stage 4] Final Optimization")
        tokenize_and_optimize(stage3_output, stage4_output, token_limit)

        logging.info("[Stage 5] Cleanup")
        cleanup_intermediate_files(
            stage1_output,
            stage3_output,
            keep_extracted,
            keep_markdown
        )

        logging.info("Pipeline Complete.")
    except Exception as e:
        logging.error(f"Pipeline failed: {str(e)}")
        # Clean up on failure unless explicitly told to keep
        cleanup_intermediate_files(stage1_output, stage3_output, keep_extracted, keep_markdown)
        raise 