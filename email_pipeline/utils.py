"""
Utility functions for the email processing pipeline.
"""

import os
import re
import base64
import shutil
import logging
import platform
import threading
import _thread
import tiktoken
import subprocess
from pathlib import Path
from email.header import decode_header
from typing import List, Optional, Tuple
from email_pipeline.config import TARGET_USER_NAME
from tqdm import tqdm

def decode_string(s: Optional[str]) -> str:
    """Decode an email header string safely."""
    if not s:
        return ""
    parts = []
    for part, charset in decode_header(s):
        if isinstance(part, bytes):
            try:
                parts.append(part.decode(charset or 'utf-8', errors='replace'))
            except:
                parts.append("[Decoding Error]")
        else:
            parts.append(str(part))
    return " ".join(parts)

def remove_all_urls(content: str) -> str:
    """Remove ALL URLs from the text (including markdown links)."""
    content = re.sub(r'https?://\S+', '', content)
    content = re.sub(r'\[([^\]]+)\]\(\)', r'\1', content)
    content = re.sub(r'\[[^\]]*\]:\s*$', '', content)
    return content

def is_user_relevant(msg, user_name: str) -> bool:
    """
    Check if user_name is sender or direct recipient (not only in CC).
    Case-insensitive match by normalizing everything to lowercase.
    """
    from_addr = decode_string(msg.get('From') or "").lower()
    to_addr = decode_string(msg.get('To') or "").lower()

    target_clean = user_name.lower().replace('.', '').replace('_', '').replace('-', '')

    def contains_target(addr: str) -> bool:
        normalized = addr.replace('.', '').replace('_', '').replace('-', '')
        return target_clean in normalized

    # Return True if user is in FROM or TO (ignoring CC presence)
    return contains_target(from_addr) or contains_target(to_addr)

def count_tokens(text: str, encoding_name="cl100k_base") -> int:
    """Count tokens via tiktoken."""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))

def has_meaningful_content(text: str) -> bool:
    """Check if text has meaningful content."""
    # Clean and split the text
    cleaned_text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = cleaned_text.strip().split()

    # Empty content is not meaningful
    if not words:
        return False

    # For other content, require at least 3 words
    return len(words) >= 3

def looks_like_binary(data: bytes) -> bool:
    """
    Extended set of binary signatures.
    """
    binary_signatures = [
        b'%PDF-', b'\x89PNG', b'\xFF\xD8\xFF', b'GIF87a', b'GIF89a',
        b'PK\x03\x04', b'\x50\x4B', b'Rar!', b'\x1F\x8B', b'\x42\x5A\x68',
        b'\xD0\xCF\x11\xE0', b'7z\xBC\xAF\x27\x1C', b'\x25\x21\x50\x53',
        b'\x00\x01\x00\x00', b'\x4F\x54\x54\x4F', b'\x00\x00\x01\x00',
    ]
    return any(data.startswith(sig) for sig in binary_signatures)

def is_unwanted_binary_type(content_type: str) -> bool:
    """Identify attachments not needed in final output (PDF, ZIP, images, etc.)."""
    unwanted_starts = [
        'application/pdf', 'image/', 'application/zip', 'application/x-compressed',
        'application/x-rar', 'application/msword', 'application/vnd.ms-',
        'application/x-7z-compressed', 'application/octet-stream'
    ]
    lower = content_type.lower()
    return any(lower.startswith(s) for s in unwanted_starts)

def is_base64_content(data: bytes | str) -> bool:
    """
    Detect if content appears to be base64 encoded.
    Works with both bytes and string input.
    """
    if isinstance(data, str):
        data = data.encode('utf-8', errors='ignore')
    if len(data) < 50:
        return False
    try:
        b64_chars = sum(1 for c in data if chr(c) in 
                       'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')
        if b64_chars / len(data) > 0.80:
            return True
        if b'=' in data[-2:]:
            content_before_padding = data[:-2]
            b64_ratio = sum(1 for c in content_before_padding if chr(c) in 
                            'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/') / len(content_before_padding)
            if b64_ratio > 0.80:
                return True
        common_patterns = [
            b'data:', b'begin-base64', b'Content-Transfer-Encoding: base64',
            b'base64,', b'B64ENCODED', b'encoded-attachment'
        ]
        if any(pattern in data for pattern in common_patterns):
            return True
        if len(data) > 100:
            try:
                sample = data[:100] + b'=' * (-len(data[:100]) % 4)
                base64.b64decode(sample)
                if b64_chars / len(data) > 0.70:
                    return True
            except:
                pass
        return False
    except:
        return False

def clean_base64_from_text(text: str) -> str:
    """Remove any base64 content embedded in text lines."""
    cleaned_lines = []
    for line in text.split('\n'):
        if is_base64_content(line):
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

class TimeoutError(Exception):
    pass

def timeout_handler():
    _thread.interrupt_main()

def process_with_timeout(func, args=(), kwargs={}, timeout_duration=300, timeout_message="Operation timed out"):
    timer = threading.Timer(timeout_duration, timeout_handler)
    try:
        timer.start()
        result = func(*args, **kwargs)
        return result
    except KeyboardInterrupt:
        raise TimeoutError(timeout_message)
    finally:
        timer.cancel()

def get_file_size_stats(sizes: List[int]) -> Tuple[float, float]:
    """
    Calculate file size statistics using a more lenient approach.
    Uses median and IQR instead of mean and standard deviation to be more robust to outliers.
    """
    if not sizes:
        return 0.0, 0.0
        
    # Sort sizes for percentile calculations
    sorted_sizes = sorted(sizes)
    n = len(sorted_sizes)
    
    # Calculate median (Q2)
    if n % 2 == 0:
        median = (sorted_sizes[n//2 - 1] + sorted_sizes[n//2]) / 2
    else:
        median = sorted_sizes[n//2]
        
    # Calculate Q1 and Q3
    q1_idx = n // 4
    q3_idx = (3 * n) // 4
    q1 = sorted_sizes[q1_idx]
    q3 = sorted_sizes[q3_idx]
    
    # Calculate IQR and use it for threshold calculation
    iqr = q3 - q1
    
    # Return median as central tendency and IQR as spread measure
    # IQR is multiplied by 0.75 to make the threshold more lenient
    return median, iqr * 0.75

def secure_delete_directory(path: str) -> None:
    """
    Securely delete a directory and its contents without moving to trash.
    Uses shred on Unix systems if available, otherwise falls back to regular delete.
    """
    path = Path(path)
    if not path.exists():
        return

    def secure_delete_file(file_path: Path) -> None:
        if platform.system() == 'Linux' or platform.system() == 'Darwin':
            try:
                # Try to use shred for secure deletion
                subprocess.run(['shred', '-u', '-z', '-n', '3', str(file_path)], check=False)
                return
            except FileNotFoundError:
                pass
        # Fallback to regular delete
        os.unlink(file_path)

    # First get list of all files
    all_files = list(path.rglob('*'))
    file_count = sum(1 for f in all_files if f.is_file())
    dir_count = sum(1 for f in all_files if f.is_dir())

    # First pass: Overwrite all files with progress bar
    if file_count > 0:
        pbar = tqdm(total=file_count, desc=f"Securely deleting files in {path.name}", unit="file")
        for file_path in path.rglob('*'):
            if file_path.is_file():
                try:
                    secure_delete_file(file_path)
                    pbar.update(1)
                except Exception as e:
                    logging.warning(f"Error deleting {file_path}: {str(e)}")
        pbar.close()

    # Second pass: Remove empty directories with progress bar
    if dir_count > 0:
        pbar = tqdm(total=dir_count, desc=f"Removing directories in {path.name}", unit="dir")
        for dir_path in sorted(path.rglob('*'), key=lambda x: len(str(x)), reverse=True):
            if dir_path.is_dir():
                try:
                    dir_path.rmdir()
                    pbar.update(1)
                except Exception as e:
                    logging.warning(f"Error removing directory {dir_path}: {str(e)}")
        pbar.close()

    # Finally remove the root directory
    try:
        path.rmdir()
    except Exception as e:
        # If directory not empty, use rmtree as fallback
        shutil.rmtree(path, ignore_errors=True) 