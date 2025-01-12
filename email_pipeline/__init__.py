"""
Email Processing Pipeline Package.

A comprehensive email processing pipeline that handles:
- PST extraction
- Thread analysis
- Content processing
- Final optimization
"""

from email_pipeline.pipeline import run_pipeline
from email_pipeline.config import TARGET_TOKEN_AMOUNT, TARGET_USER_NAME

__all__ = ['run_pipeline', 'TARGET_TOKEN_AMOUNT', 'TARGET_USER_NAME'] 