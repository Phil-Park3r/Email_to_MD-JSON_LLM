# Email to MD/JSON LLM

A robust pipeline for converting PST email archives into structured JSON and Markdown formats, optimized for Large Language Model (LLM) processing.

## 🎯 Key Features

- Converts PST files into clean, structured email threads
- Removes noise (disclaimers, duplicates, signatures)
- Handles large email bodies via intelligent splitting
- Preserves email threading and conversation context
- Optimizes output for LLM token limits
- Uses statistical methods for outlier detection

## 📋 Prerequisites

- Python 3.8+
- `readpst` utility installed
- Dependencies from `requirements.txt`

## 🚀 Quick Start

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the pipeline:
```bash
python main.py /path/to/file.pst --user-name <username> --token-limit 150000
```

## 🔧 Command Line Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `pst_file` | Path to PST file (required) | - |
| `--user-name` | Username for email filtering | "user" |
| `--token-limit` | Maximum tokens per JSON chunk | 150000 |
| `--keep-extracted` | Keep extracted .eml files | False |
| `--keep-markdown` | Keep processed Markdown files | False |
| `--stat-threshold` | Statistical threshold for outlier detection | 3.0 |

## 📁 Project Structure

```
email_pipeline/
├── __init__.py          # Package initialization
├── config.py            # Configuration settings
├── html_processing.py   # HTML conversion utilities
├── pipeline.py          # Pipeline orchestration
├── stages/             # Processing stages
│   ├── stage1_extraction.py        # PST → EML
│   ├── stage2_thread_analysis.py   # Thread mapping
│   ├── stage3_content_processing.py # Content cleanup
│   └── stage4_optimization.py      # Token optimization
└── utils.py            # Helper functions
```

## 🔄 Processing Stages

### 1. PST Extraction
- Converts PST to .eml format
- Preserves attachments and metadata
- Output: `extracted_data/`

### 2. Thread Analysis
- Maps email relationships
- Detects circular references
- Identifies thread splits
- Output: `thread_map.json`

### 3. Content Processing
- Removes disclaimers and duplicates
- Cleans forwarded text and quotes
- Preserves conversation context
- Output: `processed_markdown/*.md` + `combined_emails_stage3.json`

### 4. Final Optimization
- Statistical outlier detection
- Token-aware content splitting
- Thread-based JSON structuring
- Output: `final_json/*.json`

## 📊 Statistical Methods

The pipeline uses robust statistical methods for outlier detection:
- Median and Interquartile Range (IQR)
- Threshold formula: `median + (STATISTICAL_THRESHOLD * IQR * 0.75)`
- Configurable threshold multiplier (default: 3.0)

## 📂 Output Directory Structure

```
output_<timestamp>/
├── extracted_data/        # Raw .eml files
├── processed_markdown/    # Markdown + combined JSON
└── final_json/           # Optimized JSON chunks
```

## ⚙️ Configuration

Key settings in `config.py`:
- `TARGET_USER_NAME`: Default username for filtering
- `TARGET_TOKEN_AMOUNT`: Default token limit per chunk
- `STATISTICAL_THRESHOLD`: Outlier detection sensitivity

## 📝 Notes

- Progress is logged with tqdm progress bars
- Intermediate files are cleaned up unless specified
- JSON output is optimized for LLM token limits
- Thread relationships are preserved in final output

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
