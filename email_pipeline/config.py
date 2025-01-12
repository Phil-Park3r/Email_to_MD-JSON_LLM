"""
Configuration settings for the email processing pipeline.
"""

# User Configuration
TARGET_USER_NAME = "user"     # Default username to filter emails by
TARGET_TOKEN_AMOUNT = 150000    # Default token limit for JSON chunks

# Statistical filtering configuration
STATISTICAL_THRESHOLD = 3.0  # Default threshold for statistical filtering 