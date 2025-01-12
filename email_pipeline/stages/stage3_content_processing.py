"""
Stage 3: Content Processing functionality.
"""

import os
import re
import email.message
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from email_pipeline.utils import (
    is_user_relevant, looks_like_binary, is_unwanted_binary_type,
    is_base64_content, clean_base64_from_text, has_meaningful_content,
    remove_all_urls
)
from email_pipeline.html_processing import convert_html_to_text
from email_pipeline.stages.stage2_thread_analysis import analyze_thread_relationships

def identify_forwarded_content(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Identify and extract forwarded content sections."""
    forward_markers = [
        r"^-{3,} Forwarded message -{3,}$",
        r"^_{3,} Forwarded message _{3,}$",
        r"^From: .*\nDate: .*\nSubject: .*\nTo: .*$",
        r"^Begin forwarded message:$",
        r"^-+ Forward -+$"
    ]
    forwarded_sections = []
    cleaned_lines = []
    current_section = None
    lines = text.split('\n')
    in_forward = False

    for line in lines:
        is_marker = any(re.match(marker, line, re.IGNORECASE) for marker in forward_markers)
        if is_marker:
            if current_section:
                forwarded_sections.append(current_section)
            current_section = {"content": [], "metadata": {}}
            in_forward = True
            continue

        if in_forward:
            if line.startswith(("From:", "Date:", "Subject:", "To:")):
                key, value = line.split(":", 1)
                current_section["metadata"][key.strip()] = value.strip()
            current_section["content"].append(line)
        else:
            cleaned_lines.append(line)

    if current_section:
        forwarded_sections.append(current_section)

    return "\n".join(cleaned_lines), forwarded_sections

def handle_quote_styles(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Handle different email quote styles."""
    quote_patterns = [
        r"^>[> ]*(.+)$",
        r"^On .* wrote:$",
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} GMT[+-]\d{2}:\d{2}$",
        r"^From: .*$",
        r"^Le .* a écrit :$",
        r"^Am .* schrieb .*:$",
    ]
    quotes = []
    cleaned_lines = []
    current_quote = None
    in_quote = False
    for line in text.split('\n'):
        is_quote = any(re.match(p, line) for p in quote_patterns)
        if is_quote:
            if not current_quote:
                current_quote = {"content": [], "style": "unknown"}
            if line.startswith('>'):
                current_quote["style"] = "traditional"
            current_quote["content"].append(line)
            in_quote = True
        else:
            if in_quote and not line.strip():
                if current_quote:
                    quotes.append(current_quote)
                    current_quote = None
                in_quote = False
            if not in_quote:
                cleaned_lines.append(line)
    if current_quote:
        quotes.append(current_quote)
    return "\n".join(cleaned_lines), quotes

def fuzzy_match_content(line1: str, line2: str, threshold: float = 0.75) -> bool:
    """
    Fuzzy match two lines of text.
    Requires minimum length and adjusts threshold based on content.
    """
    from difflib import SequenceMatcher

    def normalize_text(txt: str) -> str:
        txt = re.sub(r'\s+', ' ', txt)
        txt = re.sub(r'[^\w\s]', '', txt)
        return txt.lower().strip()

    # Normalize both lines
    norm1 = normalize_text(line1)
    norm2 = normalize_text(line2)

    # Require minimum length for fuzzy matching
    if len(norm1) < 30 or len(norm2) < 30:
        return norm1 == norm2  # Require exact match for short lines

    # Calculate similarity ratio
    ratio = SequenceMatcher(None, norm1, norm2).ratio()

    # Adjust threshold based on line length
    adjusted_threshold = threshold
    if len(norm1) > 1000 or len(norm2) > 1000:
        adjusted_threshold *= 0.9  # Lower threshold for very long lines
    elif len(norm1) < 50 or len(norm2) < 50:
        adjusted_threshold *= 1.1  # Higher threshold for shorter lines

    # Additional check for lines with dates/times
    if re.search(r'\d{2}:\d{2}|\d{4}-\d{2}-\d{2}', line1) and re.search(r'\d{2}:\d{2}|\d{4}-\d{2}-\d{2}', line2):
        adjusted_threshold *= 1.2  # Require higher similarity for lines with timestamps

    return ratio >= adjusted_threshold

def deduplicate_lines_across_messages(lines: List[str], global_history: List[str], max_history: int = 20) -> List[str]:
    """Deduplicate lines across the entire thread."""
    cleaned_lines = []
    for line in lines:
        if not line.strip():
            if cleaned_lines and cleaned_lines[-1].strip():
                cleaned_lines.append(line)
            continue

        is_duplicate = any(
            fuzzy_match_content(line, gh_line, 0.85) 
            for gh_line in global_history[-max_history:]
        )
        if not is_duplicate:
            cleaned_lines.append(line)
            global_history.append(line)
    return cleaned_lines

def deduplicate_content_per_message(text: str) -> str:
    """Remove duplicates within a single message."""
    lines = text.split('\n')
    result = []
    recent_history = []

    for line in lines:
        if any(pattern in line.lower() for pattern in [
            'confidentiality notice', 'this email is intended', 'please consider the environment',
            'company registration', 'virus scanning', 'scanned for viruses'
        ]):
            continue

        if not line.strip():
            if result and result[-1].strip():
                result.append(line)
            continue

        is_dup = any(
            fuzzy_match_content(line, rh, 0.85) 
            for rh in recent_history[-5:]
        )
        if not is_dup:
            result.append(line)
            recent_history.append(line)

    return "\n".join(result)

def chunk_large_text(content: str, chunk_size_limit: int = 100000) -> List[str]:
    """
    Split large text into manageable chunks.
    Recursively splits paragraphs that exceed the size limit.
    """
    def split_paragraph(para: str, size_limit: int) -> List[str]:
        """Split a single paragraph into smaller chunks."""
        if len(para) <= size_limit:
            return [para]

        # Try to split on sentence boundaries first
        sentences = re.split(r'([.!?]+\s+)', para)
        if len(sentences) > 1:
            chunks = []
            current_chunk = []
            current_size = 0
            
            for i in range(0, len(sentences), 2):
                sentence = sentences[i]
                delimiter = sentences[i + 1] if i + 1 < len(sentences) else ""
                piece = sentence + delimiter
                piece_size = len(piece)
                
                if current_size + piece_size > size_limit:
                    if current_chunk:
                        chunks.append("".join(current_chunk))
                        current_chunk = []
                        current_size = 0
                    # If a single sentence is too large, split it on word boundaries
                    if piece_size > size_limit:
                        words = piece.split()
                        word_chunk = []
                        word_size = 0
                        for word in words:
                            word_len = len(word) + 1  # +1 for space
                            if word_size + word_len > size_limit:
                                if word_chunk:
                                    chunks.append(" ".join(word_chunk))
                                word_chunk = [word]
                                word_size = word_len
                            else:
                                word_chunk.append(word)
                                word_size += word_len
                        if word_chunk:
                            chunks.append(" ".join(word_chunk))
                    else:
                        chunks.append(piece)
                else:
                    current_chunk.append(piece)
                    current_size += piece_size
            
            if current_chunk:
                chunks.append("".join(current_chunk))
            return chunks
        
        # If no sentence boundaries, split on word boundaries
        words = para.split()
        chunks = []
        current_chunk = []
        current_size = 0
        
        for word in words:
            word_len = len(word) + 1  # +1 for space
            if current_size + word_len > size_limit:
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                current_chunk = [word]
                current_size = word_len
            else:
                current_chunk.append(word)
                current_size += word_len
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
        return chunks

    paragraphs = content.split('\n\n')
    chunks = []
    current_chunk = []
    current_size = 0

    for para in paragraphs:
        para_size = len(para) + 2  # +2 for newlines
        if para_size > chunk_size_limit:
            # First save the current chunk if it exists
            if current_chunk:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []
                current_size = 0
            
            # Split the large paragraph and add each piece as its own chunk
            para_chunks = split_paragraph(para, chunk_size_limit)
            for p in para_chunks:
                chunks.append(p)
            continue

        if current_size + para_size > chunk_size_limit:
            chunks.append('\n\n'.join(current_chunk))
            current_chunk = [para]
            current_size = para_size
        else:
            current_chunk.append(para)
            current_size += para_size

    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks

def strip_disclaimers_footers_signatures(block_lines: List[str]) -> List[str]:
    """
    Given the lines of a single email 'block' (excluding From/To/Cc lines),
    remove known disclaimers, footers, and signature lines.
    Only removes lines that exactly match known patterns.

    Returns a list of cleaned body lines.
    """
    disclaimer_patterns = [
        r"^This e-?mail (?:message )? is confidential and intended only for",
        r"^This e-?mail (?:message )? contains confidential information",
        r"^If you are not the intended recipient,? you must not",
        r"^NOTICE OF CONFIDENTIALITY:",
        r"^CONFIDENTIALITY NOTICE:",
        r"^This message was scanned for viruses and dangerous content",
        r"^This email has been scanned for viruses",
        r"^This email and any attachments are confidential",
        r"^This communication is intended only for the use of the individual",
        r"^Unauthorized review, use, disclosure or distribution is prohibited",
    ]
    disclaimer_pattern = re.compile("|".join(disclaimer_patterns), flags=re.IGNORECASE)

    signature_delimiters = [
        r"^best regards[,]?$",
        r"^kind regards[,]?$",
        r"^warm regards[,]?$",
        r"^regards[,]?$",
        r"^thanks[,]?$",
        r"^thank you[,]?$",
        r"^sincerely[,]?$",
        r"^cheers[,]?$",
        r"^--\s*$",
        r"^__+\s*$",
        r"^-\s*$",
        r"^sent from my (?:iphone|android|ipad|mobile device)$",
    ]
    signature_pattern = re.compile("|".join(signature_delimiters), flags=re.IGNORECASE)

    cleaned_body = []
    hit_signature_region = False
    disclaimer_block_start = None

    for i, line in enumerate(block_lines):
        stripped = line.strip()

        # Skip empty lines at the start
        if not stripped and not cleaned_body:
            continue

        # If we've hit a signature, skip the rest
        if hit_signature_region:
            continue

        # Check for signature markers
        if signature_pattern.match(stripped):
            hit_signature_region = True
            continue

        # Check for disclaimer start
        if disclaimer_pattern.match(stripped):
            disclaimer_block_start = i
            continue

        # If we're not in a signature region and haven't hit a disclaimer,
        # keep the line
        if disclaimer_block_start is None:
            cleaned_body.append(line)

    # Remove trailing empty lines
    while cleaned_body and not cleaned_body[-1].strip():
        cleaned_body.pop()

    return cleaned_body

def split_into_blocks(email_text: str) -> List[List[str]]:
    """
    Splits the entire email text into "blocks" based on lines starting with "From:".
    Each block will be a list of lines.

    Returns a list of blocks, where each block is a list of lines.
    """
    lines = email_text.splitlines()
    blocks = []
    current_block = []

    for line in lines:
        if re.match(r"^From:\s?", line, flags=re.IGNORECASE):
            if current_block:
                blocks.append(current_block)
            current_block = [line]
        else:
            current_block.append(line)

    if current_block:
        blocks.append(current_block)

    return blocks

def process_email_text(email_text: str) -> str:
    """
    Main pipeline to:
      1) Split the email into blocks by "From:"
      2) For each block, preserve lines that start with From/To/Cc,
         but remove disclaimers, footers, and signatures from the rest.
      3) Rejoin the cleaned blocks in the desired format.
    """
    blocks = split_into_blocks(email_text)

    cleaned_blocks = []
    for block in blocks:
        header_lines = []
        body_lines = []

        for line in block:
            if re.match(r"^(From|To|Cc):", line, flags=re.IGNORECASE):
                header_lines.append(line)
            else:
                body_lines.append(line)

        cleaned_body_lines = strip_disclaimers_footers_signatures(body_lines)

        new_block = []
        new_block.extend(header_lines)
        if cleaned_body_lines:
            new_block.append("")  # separate header from body with a blank line
            new_block.extend(cleaned_body_lines)

        cleaned_blocks.append(new_block)

    final_output_lines = []
    for i, block_lines in enumerate(cleaned_blocks):
        if i > 0:
            final_output_lines.append("")  # blank line between blocks
        final_output_lines.extend(block_lines)

    return "\n".join(final_output_lines)

def extract_email_content(msg: email.message.Message, user_name: str) -> str:
    """Extract content from email message."""
    try:
        # Add placeholders for missing metadata
        if not msg.get('Message-ID'):
            msg['Message-ID'] = f"<auto-{id(msg)}@placeholder>"
        if not msg.get('Date'):
            msg['Date'] = "Unknown Date"
        if not msg.get('From'):
            msg['From'] = "Unknown Sender"

        if not is_user_relevant(msg, user_name):
            return ""

        content_parts = []

        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type().lower()
            cdisp = (part.get("Content-Disposition") or "").lower()

            if not (ctype.startswith('text/plain') or ctype.startswith('text/html')):
                continue
            if "attachment" in cdisp or is_unwanted_binary_type(ctype):
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue
            if looks_like_binary(payload):
                continue
            if is_base64_content(payload):
                continue

            text = payload.decode('utf-8', errors='replace')
            if ctype.startswith('text/html'):
                text = convert_html_to_text(text)

            # Add footer removal here
            text = process_email_text(text)

            text, forwards = identify_forwarded_content(text)
            text, quotes = handle_quote_styles(text)

            content_parts.append(text)

            for fwd in forwards:
                if fwd.get('content'):
                    content_parts.append("\n--- Forwarded Content ---\n")
                    fwd_content = clean_base64_from_text("\n".join(fwd['content']))
                    content_parts.append(fwd_content)
            for quote in quotes:
                if quote.get('content'):
                    content_parts.append("\n--- Quoted Content ---\n")
                    quote_content = clean_base64_from_text("\n".join(quote['content']))
                    content_parts.append(quote_content)

        full_text = "\n\n".join(content_parts)
        full_text = clean_base64_from_text(full_text)
        full_text = re.sub(r'\n{3,}', '\n\n', full_text).strip()

        if len(full_text) <= 100000:
            return full_text
        else:
            big_chunks = chunk_large_text(full_text, chunk_size_limit=100000)
            return "\n\n--- Large Email Split ---\n\n".join(big_chunks)

    except Exception as e:
        logging.error(f"Error extracting content: {str(e)}")
        return ""

def process_content(eml_paths: List[Path], output_dir: str, user_name: str) -> None:
    """Main function for content processing stage."""
    os.makedirs(output_dir, exist_ok=True)
    logging.info(f"Stage 3: Found {len(eml_paths)} .eml files")

    thread_map = analyze_thread_relationships(eml_paths, user_name)
    logging.info(f"Stage 3: Built thread map with {len(thread_map)} messages")

    all_emails_for_stage3 = []  # Accumulate full metadata across all threads

    thread_groups: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for msg_id, data in thread_map.items():
        subj = data.get('subject', '')
        clean_subj = re.sub(r'^(?:Re|Fwd|Fw|Forward):\s*', '', subj, flags=re.IGNORECASE)
        thread_groups.setdefault(clean_subj, []).append((msg_id, data))

    for thread_subj in thread_groups:
        thread_groups[thread_subj].sort(key=lambda x: x[1].get('date', ''))

    logging.info(f"Stage 3: Grouped into {len(thread_groups)} conversation threads")

    total_threads = len(thread_groups)
    pbar = tqdm(total=total_threads, desc="Processing threads", unit="thread")
    
    for thread_subj, messages in thread_groups.items():
        global_history: List[str] = []
        thread_text = []

        for msg_id, meta in messages:
            eml_file = Path(meta['path'])
            if not eml_file.exists():
                logging.debug(f"Skipping missing file {eml_file}")
                continue

            with open(eml_file, 'rb') as f:
                raw_msg = f.read()
            if not raw_msg.strip():
                logging.debug(f"Skipping empty file {eml_file}")
                continue
            msg = email.message_from_bytes(raw_msg)

            content = extract_email_content(msg, user_name)
            if not content.strip():
                logging.debug(f"Skipping {eml_file}: No content after extraction")
                continue

            content = deduplicate_content_per_message(content)
            content = remove_all_urls(content)

            if not has_meaningful_content(content):
                content = "[No meaningful content]"

            lines = content.split('\n')
            cleaned = deduplicate_lines_across_messages(lines, global_history, max_history=20)
            thread_text.extend(cleaned)
            thread_text.append("---")

            # Collect each message from this thread with metadata + final text
            all_emails_for_stage3.append({
                "message_id": meta.get("message_id", ""),
                "from": meta.get("from", ""),
                "to": meta.get("to", ""),
                "date": meta.get("date", ""),
                "subject": meta.get("subject", ""),
                "content": "\n".join(cleaned)
            })

        while thread_text and (not thread_text[-1].strip() or thread_text[-1].strip() == "---"):
            thread_text.pop()

        thread_text_joined = "\n".join(thread_text).strip()
        if not thread_text_joined:
            pbar.update(1)
            continue

        safe_name = re.sub(r'[^a-zA-Z0-9_\-]+', '_', thread_subj)[:50]
        out_path = os.path.join(output_dir, safe_name + ".md")
        with open(out_path, 'w', encoding='utf-8') as wf:
            wf.write(f"# {thread_subj}\n\n")
            wf.write(thread_text_joined + "\n")

        pbar.update(1)
    
    pbar.close()
    logging.info("Stage 3: Single .md per thread created successfully.")

    # Write collected metadata to JSON
    import json
    combined_json = os.path.join(output_dir, "combined_emails_stage3.json")
    with open(combined_json, 'w', encoding='utf-8') as jf:
        json.dump(all_emails_for_stage3, jf, indent=2)
    logging.info(
        f"Stage 3: Wrote {len(all_emails_for_stage3)} records to {combined_json}"
    ) 