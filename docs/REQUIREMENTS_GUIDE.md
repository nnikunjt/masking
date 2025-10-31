# Requirements Files Guide

## Active Requirements Files

### 1. `requirements.txt` (Root Directory)
**Use for**: Local development with v6 hybrid approach

**Contents**:
- `boto3` - AWS S3 integration
- `pdfplumber` - Text extraction for horizontal text
- `pytesseract` - OCR fallback when text extraction fails
- `PyMuPDF` (fitz) - Vertical text detection
- `Pillow` - Image processing and masking
- `pyzbar` - QR code and barcode detection
- `requests` - HTTP requests for downloading PDFs
- `opencv-python` - Image processing
- `numpy` - Numerical operations

**Installation**:
```bash
pip install -r requirements.txt
```

---

### 2. `lambda/requirements.txt` (Lambda Directory)
**Use for**: AWS Lambda deployment (production)

**Contents**:
- Same as root `requirements.txt` but **excludes `pytesseract`**
- Optimized for Lambda package size
- Uses pdfplumber + PyMuPDF hybrid approach

**Installation**:
```bash
cd lambda
pip install -r requirements.txt
```

**Note**: Lambda uses Docker builds which handle Tesseract installation separately (see Dockerfile)

---

## Archived Requirements Files (in `docs/`)

### `docs/requirements_pymupdf.txt`
**Use for**: v7 PyMuPDF-only approach (experimental/archived)

**Contents**:
- `PyMuPDF` only for text detection (both horizontal and vertical)
- No pdfplumber or pytesseract
- More efficient but potentially less accurate for some PDFs

### `docs/requirements_pymupdf_minimal.txt`
**Use for**: Minimal PyMuPDF implementation (experimental)

**Contents**:
- Only essential packages: PyMuPDF, requests, boto3
- Very lightweight but limited functionality

---

## Recommendation

- **Local Development**: Use `requirements.txt` (root)
- **Lambda Deployment**: Use `lambda/requirements.txt`
- **Archived Versions**: Reference files in `docs/` for historical purposes

