"""
Stage 2: Thread Analysis functionality.
"""

import os
import json
import email
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from email_pipeline.utils import decode_string, is_user_relevant

def read_eml_metadata(eml_file: Path, user_name: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Read metadata from an EML file."""
    try:
        with open(eml_file, 'rb') as f:
            data = f.read()
            if not data.strip():
                return None
        msg = email.message_from_bytes(data)

        # Generate fallback Message-ID if missing
        message_id = (msg.get('Message-ID') or f"generated-{eml_file.stem}").strip()
        if not message_id:
            message_id = f"auto-id-{eml_file.stem}"

        # Use fallback date if missing
        date = msg.get('Date') or "Unknown Date"
        
        # Use fallback From address if missing
        from_addr = decode_string(msg.get('From') or f"unknown-sender-{eml_file.stem}")

        if not is_user_relevant(msg, user_name):
            return None

        in_reply_to = (msg.get('In-Reply-To') or "").strip()
        references = (msg.get('References') or "").strip().split()
        subject = decode_string(msg.get('Subject') or "No Subject")
        to_addr = decode_string(msg.get('To') or "No Recipients")

        return message_id, {
            'path': str(eml_file),
            'message_id': message_id,
            'in_reply_to': in_reply_to,
            'references': references,
            'date': date,
            'subject': subject,
            'from': from_addr,
            'to': to_addr,
            'children': [],
            'split_thread': False
        }
    except Exception as e:
        logging.error(f"Error reading metadata from {eml_file}: {str(e)}")
        return None

def handle_thread_splits(thread_map: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Identify and handle thread splits."""
    splits = {}
    for msg_id, data in thread_map.items():
        children = data.get('children', [])
        if len(children) > 1:
            splits[msg_id] = children
    return splits

def detect_circular_references(thread_map: Dict[str, Dict[str, Any]]) -> List[str]:
    """Detect and return list of message IDs involved in circular references."""
    def find_cycle(msg_id: str, visited: set, path: set) -> Optional[List[str]]:
        if msg_id in path:
            return list(path)
        if msg_id in visited:
            return None
        visited.add(msg_id)
        path.add(msg_id)
        if msg_id in thread_map:
            nxt_msgs = set()
            if thread_map[msg_id].get('in_reply_to'):
                nxt_msgs.add(thread_map[msg_id]['in_reply_to'])
            nxt_msgs.update(thread_map[msg_id].get('references', []))
            for nxt in nxt_msgs:
                cycle = find_cycle(nxt, visited, path)
                if cycle:
                    return cycle
        path.remove(msg_id)
        return None
    
    circular_refs = set()
    visited = set()
    for mid in thread_map:
        if mid not in visited:
            cycle = find_cycle(mid, visited, set())
            if cycle:
                circular_refs.update(cycle)
    return list(circular_refs)

def analyze_thread_relationships(eml_paths: List[Path], user_name: str) -> Dict[str, Dict[str, Any]]:
    """Analyze relationships between email threads."""
    thread_map: Dict[str, Dict[str, Any]] = {}
    
    # Setup progress bar for metadata extraction
    pbar = tqdm(total=len(eml_paths), desc="Reading email metadata", unit="email")
    
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(read_eml_metadata, f, user_name) for f in eml_paths]
        for fut in as_completed(futures):
            pbar.update(1)
            result = fut.result()
            if result:
                msg_id, metadata = result
                if len(metadata.get('references', [])) > 50:
                    # Truncate references instead of skipping
                    metadata['references'] = metadata['references'][:50]
                    logging.warning(f"Truncated references for {msg_id} to 50 items")
                thread_map[msg_id] = metadata
    
    pbar.close()
    
    # Setup progress bar for relationship analysis
    analysis_pbar = tqdm(total=3, desc="Analyzing relationships", unit="step")
    
    # Step 1: Handle circular references
    if len(thread_map) < 1000:
        circular_refs = detect_circular_references(thread_map)
        for m_id in circular_refs:
            if m_id in thread_map:
                refs = thread_map[m_id].get('references', [])
                if refs:
                    # Preserve at least one reference
                    thread_map[m_id]['references'] = [refs[0]]
                if thread_map[m_id]['in_reply_to'] in circular_refs:
                    thread_map[m_id]['in_reply_to'] = refs[0] if refs else ""
    analysis_pbar.update(1)
    
    # Step 2: Build parent-child relationships
    for mid, data in thread_map.items():
        parent_id = data['in_reply_to']
        if parent_id and parent_id in thread_map:
            thread_map[parent_id]['children'].append(mid)
    analysis_pbar.update(1)
    
    # Step 3: Handle thread splits
    splits = handle_thread_splits(thread_map)
    for parent_id in splits:
        if parent_id in thread_map:
            thread_map[parent_id]['split_thread'] = True
    analysis_pbar.update(1)
    
    analysis_pbar.close()
    return thread_map

def analyze_threads(raw_output_dir: str, user_name: str) -> None:
    """Main function for thread analysis stage."""
    eml_files = list(Path(raw_output_dir).rglob("*.eml"))
    logging.info(f"Stage 2: Found {len(eml_files)} .eml files")
    
    threads = analyze_thread_relationships(eml_files, user_name)
    logging.info(f"Stage 2: Analyzed {len(threads)} relevant messages")
    
    outpath = os.path.join(raw_output_dir, "thread_map.json")
    with open(outpath, 'w', encoding='utf-8') as wf:
        json.dump(threads, wf, indent=2)
    logging.info(f"Thread analysis complete: {outpath}") 