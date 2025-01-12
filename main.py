"""
Main entry point for the email processing pipeline.
"""

import os
import argparse
import logging
from datetime import datetime
from pathlib import Path
from tqdm import tqdm
from email_pipeline import run_pipeline
from email_pipeline.config import TARGET_TOKEN_AMOUNT, TARGET_USER_NAME, STATISTICAL_THRESHOLD

class TqdmLoggingHandler(logging.Handler):
    def __init__(self):
        super().__init__()

    def emit(self, record):
        try:
            msg = self.format(record)
            tqdm.write(msg)
            self.flush()
        except Exception:
            self.handleError(record)

def setup_logging():
    """Configure logging with tqdm support."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[TqdmLoggingHandler()]
    )

def create_timestamped_dir() -> Path:
    """Create a timestamped directory for this run."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"output_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Email Processing Pipeline")
    parser.add_argument("pst_file", help="Path to the PST file.")
    parser.add_argument("--user-name", default=TARGET_USER_NAME,
                       help=f"Username to filter emails by (default: {TARGET_USER_NAME})")
    parser.add_argument("--token-limit", type=int, default=TARGET_TOKEN_AMOUNT,
                       help=f"Token limit for final JSON chunks (default: {TARGET_TOKEN_AMOUNT})")
    parser.add_argument("--keep-extracted", action="store_true", 
                       help="Keep the extracted data folder after processing")
    parser.add_argument("--keep-markdown", action="store_true",
                       help="Keep the processed markdown folder after processing")
    parser.add_argument("--stat-threshold", type=float, default=STATISTICAL_THRESHOLD,
                       help=f"Statistical filtering threshold multiplier (default: {STATISTICAL_THRESHOLD})")
    args = parser.parse_args()

    setup_logging()

    # Create timestamped directory structure
    output_dir = create_timestamped_dir()
    stage1_dir = output_dir / "extracted_data"
    stage3_dir = output_dir / "processed_markdown"
    stage4_dir = output_dir / "final_json"

    logging.info(f"Created output directory: {output_dir}")

    # Update config values with CLI arguments
    import email_pipeline.config as config
    config.STATISTICAL_THRESHOLD = args.stat_threshold

    run_pipeline(
        args.pst_file,
        str(stage1_dir),
        None,  # stage2 output uses stage1 directory
        str(stage3_dir),
        str(stage4_dir),
        args.token_limit,
        args.keep_extracted,
        args.keep_markdown,
        args.user_name
    )

if __name__ == "__main__":
    main() 