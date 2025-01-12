"""
Stage 4: Final Optimization functionality.
"""

import os
import re
import json
import copy
import logging
from pathlib import Path
from typing import List, Dict, Any, Tuple
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from email_pipeline.config import STATISTICAL_THRESHOLD
from email_pipeline.utils import count_tokens, has_meaningful_content

def calculate_size_threshold(sizes: List[int], std_dev_threshold: float = None) -> float:
    """
    Calculate size threshold using median and IQR for more robust outlier detection.
    Uses 75% of IQR to be more lenient with thresholds.
    """
    if not sizes:
        return 0.0
        
    # Use configured threshold if none provided
    if std_dev_threshold is None:
        std_dev_threshold = STATISTICAL_THRESHOLD
        
    # Sort for percentile calculations
    sorted_sizes = sorted(sizes)
    n = len(sorted_sizes)
    
    # Calculate median
    mid = n // 2
    median = (sorted_sizes[mid] + sorted_sizes[~mid]) / 2 if n % 2 == 0 \
            else sorted_sizes[mid]
    
    # Calculate Q1 and Q3
    q1_pos = n // 4
    q3_pos = (3 * n) // 4
    q1 = sorted_sizes[q1_pos]
    q3 = sorted_sizes[q3_pos]
    
    # Calculate IQR and threshold
    iqr = q3 - q1
    threshold = median + (std_dev_threshold * iqr * 0.75)
    
    # Print statistical calculations
    print("\nStatistical Filtering Calculations:")
    print("--------------------------------")
    print(f"Total samples: {n}")
    print(f"Median size: {median:.1f}")
    print(f"Q1 (25th percentile): {q1:.1f}")
    print(f"Q3 (75th percentile): {q3:.1f}")
    print(f"IQR: {iqr:.1f}")
    print(f"Threshold multiplier: {std_dev_threshold}")
    print(f"Calculated threshold: {threshold:.1f}")
    print(f"Min size in dataset: {sorted_sizes[0]:.1f}")
    print(f"Max size in dataset: {sorted_sizes[-1]:.1f}\n")
    
    return threshold

def flatten_records_by_thread(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group messages into threads based on normalized subject lines.
    Handles malformed records gracefully and sorts messages by date within threads.
    Removes message_ids only after using them for organization.
    """
    threads_map = {}
    malformed = []
    
    # Group messages by normalized subject
    for record in records:
        try:
            # Handle missing subject gracefully
            subject = record.get("subject", "")
            if not isinstance(subject, str):
                subject = str(subject) if subject is not None else ""
            
            # Normalize subject by removing Re:, Fwd:, etc.
            base_subj = re.sub(r'^(?:Re|Fwd|Fw|Forward):\s*', '', 
                             subject, 
                             flags=re.IGNORECASE)
            base_subj = base_subj.strip() or "NoSubjectThread"
            
            if base_subj not in threads_map:
                threads_map[base_subj] = []
            threads_map[base_subj].append(record)
        except Exception as e:
            logging.warning(f"Error processing record {record.get('message_id', 'unknown')}: {str(e)}")
            malformed.append(record)
    
    # Create thread objects with date-sorted messages
    flattened = []
    for subj, msgs in threads_map.items():
        try:
            # Sort messages by date first while we still have message_ids for reference
            sorted_msgs = sorted(msgs, key=lambda x: x.get("date", ""))
            
            # Now remove message_ids for final output to reduce token count
            cleaned_msgs = [{k: v for k, v in msg.items() if k != 'message_id'} 
                          for msg in sorted_msgs]
            
            flattened.append({
                "thread_id": subj,
                "messages": cleaned_msgs
            })
        except Exception as e:
            logging.warning(f"Error sorting thread {subj}: {str(e)}")
            # Still include the thread, just unsorted but still remove message_ids
            cleaned_msgs = [{k: v for k, v in msg.items() if k != 'message_id'} 
                          for msg in msgs]
            flattened.append({
                "thread_id": subj,
                "messages": cleaned_msgs
            })
    
    # Handle malformed records (also removing message_ids)
    for i, record in enumerate(malformed):
        cleaned_record = {k: v for k, v in record.items() if k != 'message_id'}
        flattened.append({
            "thread_id": f"malformed_{i}",
            "messages": [cleaned_record]
        })
    
    logging.info(
        f"Thread grouping results:\n"
        f"- Total threads: {len(flattened)}\n"
        f"- Malformed records: {len(malformed)}"
    )
    return flattened

def filter_outlier_json(json_path: Path, std_dev_threshold: float = 3.0) -> List[Dict[str, Any]]:
    """
    Filter out records with content size above threshold.
    Now uses median/IQR based threshold calculation for more robust outlier detection.
    """
    large_files_dir = json_path.parent / "large_files"
    large_files_dir.mkdir(exist_ok=True)

    if not json_path.is_file():
        logging.error(f"No JSON file found: {json_path}")
        return []

    with open(json_path, 'r', encoding='utf-8') as f:
        records = json.load(f)
    if not records:
        return []

    sizes = [len(r.get("content","")) for r in records]
    size_threshold = calculate_size_threshold(sizes, std_dev_threshold)

    filtered = []
    moved = []
    outliers_path = large_files_dir / "outliers.json"
    for rec in records:
        c_len = len(rec.get("content",""))
        if c_len > size_threshold:
            with open(outliers_path, 'a', encoding='utf-8') as wf:
                json.dump(rec, wf)
                wf.write("\n")
            moved.append(rec.get("message_id",""))
        else:
            filtered.append(rec)

    print(f"\nJSON size filtering results:")
    print(f"- Records total: {len(records)}")
    print(f"- Within threshold: {len(filtered)}")
    print(f"- Moved to {outliers_path.name}: {len(moved)}")
    print(f"- Threshold: {size_threshold:.1f} chars\n")
    
    return filtered

def split_record_content(record: Dict[str, Any], token_limit: int) -> List[Dict[str, Any]]:
    from copy import deepcopy
    import json
    r_str = json.dumps(record)
    r_tokens = count_tokens(r_str)
    if r_tokens <= token_limit:
        return [record]
    paragraphs = record.get("content","").split("\n\n")
    partials = []
    buffer_p = []
    for para in paragraphs:
        draft = deepcopy(record)
        joined = ( ("\n\n".join(buffer_p) + "\n\n" + para) if buffer_p else para )
        draft["content"] = joined
        if count_tokens(json.dumps(draft)) > token_limit:
            if not buffer_p:
                # optional fallback to sentence/word-level
                pass
            else:
                final = deepcopy(record)
                final["content"] = "\n\n".join(buffer_p)
                partials.append(final)
                buffer_p = [para]
        else:
            buffer_p.append(para)
    if buffer_p:
        final = deepcopy(record)
        final["content"] = "\n\n".join(buffer_p)
        partials.append(final)

    result = []
    for p in partials:
        result.extend(split_record_content(p, token_limit))
    return result

def save_batch(batch: List[Dict[str, Any]], output_dir: str, index: int) -> None:
    """Save a batch of records to a JSON file."""
    out_path = os.path.join(output_dir, f"optimized_{index:03d}.json")
    with open(out_path, 'w', encoding='utf-8') as wf:
        json.dump(batch, wf, indent=2)
    logging.info(f"Created {out_path} with {len(batch)} records")

def split_thread_by_tokens(thread: Dict[str, Any], token_limit: int) -> List[Dict[str, Any]]:
    """
    Split a thread into smaller parts if it exceeds the token limit.
    Preserves thread context by keeping messages together when possible.
    """
    # Check if entire thread fits
    thread_str = json.dumps(thread)
    thread_tokens = count_tokens(thread_str)
    if thread_tokens <= token_limit:
        return [thread]
    
    # Split into partial threads
    partials = []
    current = {"thread_id": thread["thread_id"], "messages": []}
    current_tokens = 0
    
    for msg in thread["messages"]:
        msg_str = json.dumps(msg)
        msg_tokens = count_tokens(msg_str)
        
        if msg_tokens > token_limit:
            # If a single message is too large, try to split its content
            logging.warning(f"Message too large ({msg_tokens} tokens), splitting content: {msg.get('message_id', '')}")
            split_contents = split_message_content(msg, token_limit)
            for split_msg in split_contents:
                split_tokens = count_tokens(json.dumps(split_msg))
                if current_tokens + split_tokens > token_limit:
                    if current["messages"]:
                        partials.append(copy.deepcopy(current))
                        current = {"thread_id": thread["thread_id"], "messages": []}
                        current_tokens = 0
                current["messages"].append(split_msg)
                current_tokens += split_tokens
            continue
            
        if current_tokens + msg_tokens > token_limit:
            if current["messages"]:
                partials.append(copy.deepcopy(current))
                current = {"thread_id": thread["thread_id"], "messages": []}
                current_tokens = 0
            
        current["messages"].append(msg)
        current_tokens += msg_tokens
    
    if current["messages"]:
        partials.append(copy.deepcopy(current))
    
    # Log splitting results
    logging.info(
        f"Thread splitting results for {thread['thread_id']}:\n"
        f"- Original size: {thread_tokens} tokens\n"
        f"- Split into: {len(partials)} parts"
    )
    
    return partials

def split_message_content(message: Dict[str, Any], token_limit: int) -> List[Dict[str, Any]]:
    """
    Split a single message's content if it exceeds the token limit.
    Preserves message metadata while splitting content into manageable chunks.
    """
    content = message.get("content", "")
    if not content:
        return [message]
    
    # First try paragraph-based splitting
    paragraphs = content.split("\n\n")
    splits = []
    current_content = []
    current_tokens = 0
    base_msg = {k: v for k, v in message.items() if k != "content"}
    
    for para in paragraphs:
        # Create a test message with current content plus new paragraph
        test_content = "\n\n".join(current_content + [para])
        test_msg = {**base_msg, "content": test_content}
        test_tokens = count_tokens(json.dumps(test_msg))
        
        if test_tokens > token_limit:
            if current_content:
                # Save current batch
                new_msg = {**base_msg, "content": "\n\n".join(current_content)}
                splits.append(new_msg)
                current_content = [para]
                current_tokens = count_tokens(json.dumps({**base_msg, "content": para}))
            else:
                # Single paragraph is too large, fall back to sentence splitting
                sentences = re.split(r'([.!?]+(?:\s+|$))', para)
                sentence_content = []
                for i in range(0, len(sentences), 2):
                    if i + 1 < len(sentences):
                        sentence = sentences[i] + sentences[i + 1]
                    else:
                        sentence = sentences[i]
                    
                    test_content = "\n".join(sentence_content + [sentence])
                    test_msg = {**base_msg, "content": test_content}
                    test_tokens = count_tokens(json.dumps(test_msg))
                    
                    if test_tokens > token_limit:
                        if sentence_content:
                            new_msg = {**base_msg, "content": "\n".join(sentence_content)}
                            splits.append(new_msg)
                            sentence_content = [sentence]
                        else:
                            # Even a single sentence is too large
                            logging.warning(f"Sentence too large in message {message.get('message_id', '')}")
                            words = sentence.split()
                            word_content = []
                            for word in words:
                                test_content = " ".join(word_content + [word])
                                test_msg = {**base_msg, "content": test_content}
                                test_tokens = count_tokens(json.dumps(test_msg))
                                
                                if test_tokens > token_limit:
                                    if word_content:
                                        new_msg = {**base_msg, "content": " ".join(word_content)}
                                        splits.append(new_msg)
                                        word_content = [word]
                                    else:
                                        # Single word is too large, skip it
                                        logging.warning(f"Skipping oversized word in message {message.get('message_id', '')}")
                                else:
                                    word_content.append(word)
                            
                            if word_content:
                                new_msg = {**base_msg, "content": " ".join(word_content)}
                                splits.append(new_msg)
                    else:
                        sentence_content.append(sentence)
                
                if sentence_content:
                    new_msg = {**base_msg, "content": "\n".join(sentence_content)}
                    splits.append(new_msg)
        else:
            current_content.append(para)
            current_tokens = test_tokens
    
    if current_content:
        new_msg = {**base_msg, "content": "\n\n".join(current_content)}
        splits.append(new_msg)
    
    # Add part numbers to split messages
    for i, split in enumerate(splits, 1):
        split["part"] = f"{i}/{len(splits)}"
    
    return splits

def save_thread_batch(threads: List[Dict[str, Any]], output_dir: str, batch_index: int) -> None:
    """
    Save a batch of thread objects to a JSON file.
    Includes thread metadata and ensures proper formatting.
    """
    if not threads:
        return
        
    out_path = os.path.join(output_dir, f"final_threads_{batch_index:03d}.json")
    
    # Add batch metadata
    batch_data = {
        "batch_id": batch_index,
        "thread_count": len(threads),
        "message_count": sum(len(t["messages"]) for t in threads),
        "threads": threads
    }
    
    with open(out_path, 'w', encoding='utf-8') as wf:
        json.dump(batch_data, wf, indent=2, ensure_ascii=False)
    
    logging.info(
        f"Created {out_path}:\n"
        f"- Threads: {len(threads)}\n"
        f"- Total messages: {batch_data['message_count']}"
    )

def process_thread_batch(threads: List[Dict[str, Any]], output_dir: str, token_limit: int) -> None:
    """
    Process and save a batch of threads while respecting token limits.
    Handles thread splitting and ensures proper batch sizes.
    """
    current_batch = []
    current_tokens = 0
    batch_count = 1
    
    for thread in tqdm(threads, desc="Processing threads"):
        thread_str = json.dumps(thread)
        thread_tokens = count_tokens(thread_str)
        
        if thread_tokens > token_limit:
            # Split oversized thread
            split_threads = split_thread_by_tokens(thread, token_limit)
            for split in split_threads:
                split_tokens = count_tokens(json.dumps(split))
                if current_tokens + split_tokens > token_limit:
                    save_thread_batch(current_batch, output_dir, batch_count)
                    batch_count += 1
                    current_batch = []
                    current_tokens = 0
                current_batch.append(split)
                current_tokens += split_tokens
        else:
            if current_tokens + thread_tokens > token_limit:
                save_thread_batch(current_batch, output_dir, batch_count)
                batch_count += 1
                current_batch = []
                current_tokens = 0
            current_batch.append(thread)
            current_tokens += thread_tokens
    
    if current_batch:
        save_thread_batch(current_batch, output_dir, batch_count)
    
    return batch_count

def tokenize_and_optimize(md_dir: str, output_dir: str, token_limit: int, threshold_multiplier: float = None) -> None:
    """
    Process and optimize email threads while respecting token limits.
    Now handles thread-based processing and content splitting.
    
    Args:
        md_dir: Input directory with markdown files
        output_dir: Output directory for optimized files
        token_limit: Maximum tokens per file
        threshold_multiplier: Optional custom threshold multiplier (overrides config)
    """
    os.makedirs(output_dir, exist_ok=True)

    input_json = Path(md_dir) / "combined_emails_stage3.json"
    valid_records = filter_outlier_json(input_json, threshold_multiplier)
    if not valid_records:
        logging.error("No records to process after JSON filtering")
        return

    # Group records into threads
    threads = flatten_records_by_thread(valid_records)
    logging.info(f"Stage 4: Processing {len(threads)} threads...")

    # Process threads in batches
    batch_count = process_thread_batch(threads, output_dir, token_limit)
    
    logging.info(
        f"Stage 4: Completed processing:\n"
        f"- Input threads: {len(threads)}\n"
        f"- Output batches: {batch_count}"
    ) 