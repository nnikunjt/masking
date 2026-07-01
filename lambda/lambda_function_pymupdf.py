import json
import requests
import fitz  # PyMuPDF for efficient text detection
from PIL import ImageFilter, ImageDraw, Image, ImageEnhance
from io import BytesIO
from pyzbar.pyzbar import decode
import re
import boto3
from botocore.exceptions import ClientError
import hashlib
import os
import time
from datetime import datetime
import math
from urllib.parse import urlparse
import numpy as np
import cv2

# Initialize S3 client outside the handler for better performance
s3_client = boto3.client('s3')
textract_client = boto3.client('textract', region_name=os.environ.get('AWS_REGION', 'us-east-1'))

# Get bucket name from environment variable
BUCKET_NAME = os.environ.get('AWS_S3_BUCKET', 'masked-certificates-bucket')

# Text detection parameters
ANGLE_TOLERANCE_DEG = 12        
PADDING = 1.5                   

def log_timing(operation, start_time):
    """Log timing for operations"""
    end_time = time.time()
    duration = end_time - start_time
    print(f"[{datetime.now()}] {operation}: {duration:.2f} seconds")

def detect_file_type(url):
    """Detect file type from URL extension"""
    parsed_url = urlparse(url)
    path = parsed_url.path.lower()
    
    # Check for image extensions
    if path.endswith(('.jpg', '.jpeg')):
        return 'image', 'jpeg'
    elif path.endswith('.png'):
        return 'image', 'png'
    elif path.endswith('.pdf'):
        return 'pdf', 'pdf'
    else:
        # Default to PDF for backward compatibility
        return 'pdf', 'pdf'

def download_pdf_from_url(url):
    """Download PDF from URL and return bytes"""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF from URL. Status code: {response.status_code}")
    return BytesIO(response.content)

def download_image_from_url(url):
    """Download image from URL and return bytes and PIL Image"""
    response = requests.get(url, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Failed to download image from URL. Status code: {response.status_code}")
    
    image_bytes = BytesIO(response.content)
    image = Image.open(image_bytes)
    image_bytes.seek(0)  # Reset for reuse
    
    return image_bytes, image

def upload_image_to_s3_temp(image_bytes, original_url):
    """Upload image to S3 temporary location for Textract"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    url_hash = hashlib.md5(original_url.encode()).hexdigest()[:8]
    
    # Get file extension from URL
    parsed_url = urlparse(original_url)
    path = parsed_url.path
    ext = os.path.splitext(path)[1] or '.jpg'
    if not ext.startswith('.'):
        ext = '.jpg'
    
    s3_key = f"textract_temp/{timestamp}_{url_hash}{ext}"
    
    print(f"Uploading to S3 for Textract: s3://{BUCKET_NAME}/{s3_key}")
    s3_client.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=image_bytes.getvalue(),
        ContentType=f'image/{ext[1:].lower()}'
    )
    
    return s3_key

def call_textract_for_image(s3_key):
    """Call AWS Textract DetectDocumentText API"""
    print(f"Calling AWS Textract for: s3://{BUCKET_NAME}/{s3_key}")
    
    response = textract_client.detect_document_text(
        Document={
            'S3Object': {
                'Bucket': BUCKET_NAME,
                'Name': s3_key
            }
        }
    )
    
    return response

def convert_textract_coordinates(block, image_width, image_height):
    """Convert Textract normalized coordinates to pixel coordinates for masking"""
    bbox = block['Geometry']['BoundingBox']
    
    # Textract coordinates are normalized (0-1)
    left = bbox['Left']
    top = bbox['Top']
    width = bbox['Width']
    height = bbox['Height']
    
    # Convert to pixel coordinates
    x0 = int(left * image_width)
    y0 = int(top * image_height)
    x1 = int((left + width) * image_width)
    y1 = int((top + height) * image_height)
    
    return {
        'x0': x0,
        'y0': y0,
        'x1': x1,
        'y1': y1
    }

def parse_textract_response(textract_response, image):
    """Parse Textract response and extract text with bounding boxes at word level"""
    image_width, image_height = image.size
    word_positions = []
    
    blocks = textract_response.get('Blocks', [])
    
    for block in blocks:
        block_type = block.get('BlockType')
        
        # Process WORD blocks (primary focus for masking)
        if block_type == 'WORD':
            # Get text
            text = block.get('Text', '').strip()
            if not text:
                continue
            
            # Convert coordinates
            coords = convert_textract_coordinates(block, image_width, image_height)
            
            position = {
                'text': text,
                'x0': coords['x0'],
                'y0': coords['y0'],
                'x1': coords['x1'],
                'y1': coords['y1']
            }
            
            word_positions.append(position)
    
    return word_positions

def find_longest_contiguous_match(target, text):
    """
    Find the longest contiguous subsequence of target that appears in text.
    Returns the length of the longest match.
    
    Example:
    - target="671433113", text="LG6714331" -> returns 7 (matches "6714331")
    """
    target_lower = target.lower().strip()
    text_lower = text.lower().strip()
    
    if not target_lower or not text_lower:
        return 0
    
    max_match = 0
    
    # Try matching target starting at each position in text
    for start_pos in range(len(text_lower)):
        match_len = 0
        target_idx = 0
        
        # Try to match target from this starting position
        for text_idx in range(start_pos, len(text_lower)):
            if target_idx < len(target_lower):
                if text_lower[text_idx] == target_lower[target_idx]:
                    match_len += 1
                    target_idx += 1
                else:
                    # Mismatch - but we've found a contiguous match so far
                    break
        
        max_match = max(max_match, match_len)
    
    return max_match

def is_subsequence_match(target, text):
    target_lower = target.lower().strip()
    text_lower = text.lower().strip()
    
    # Exact match
    if target_lower == text_lower:
        return True
    
    # Check if target is a contiguous substring (allows prefix/suffix, no gaps in middle)
    # The 'in' operator checks for contiguous substring, which is exactly what we need
    if target_lower in text_lower:
        return True
    
    # Fuzzy matching: if most of the sequence matches (70% threshold)
    if len(target_lower) >= 3:  # Only do fuzzy matching for longer targets
        longest_match = find_longest_contiguous_match(target_lower, text_lower)
        match_ratio = longest_match / len(target_lower) if len(target_lower) > 0 else 0
        
        # If 70% or more of target matches, consider it a match
        if match_ratio >= 0.70:
            print(f"    Fuzzy match: {longest_match}/{len(target_lower)} chars match ({match_ratio*100:.1f}%)")
            return True
    
    return False

def search_target_text_in_words(word_positions, target_text):
    """
    Search for target text in word-level positions and return matching ones.
    Matches if target text is:
    1. Exact match
    2. Substring (allows prefix/suffix)
    3. Contiguous subsequence (allows extra chars at start/end, not in middle)
    
    Also handles cases where target might be split across multiple words or have spaces.
    """
    if not target_text:
        return []
    
    matching_positions = []
    target_clean = target_text.strip()
    
    # First, try matching individual words
    for pos in word_positions:
        text = pos['text'].strip()
        
        # Remove common special characters for matching
        text_clean = re.sub(r'[^\w]', '', text)  # Remove non-word chars
        target_clean_no_special = re.sub(r'[^\w]', '', target_clean)
        
        # Use subsequence matching logic
        if is_subsequence_match(target_clean_no_special, text_clean):
            matching_positions.append(pos)
            print(f"    Matched: '{text}' contains target '{target_text}'")
    
    # If no matches found, try matching across consecutive words
    # This handles cases where Textract splits the number across words
    if not matching_positions and len(word_positions) > 1:
        target_clean_no_special = re.sub(r'[^\w]', '', target_clean)
        
        # Try combining 2-3 consecutive words
        for i in range(len(word_positions) - 1):
            # Try 2 words
            combined_text = word_positions[i]['text'] + word_positions[i+1]['text']
            combined_clean = re.sub(r'[^\w]', '', combined_text)
            
            if is_subsequence_match(target_clean_no_special, combined_clean):
                # Add both words to matching positions
                matching_positions.append(word_positions[i])
                matching_positions.append(word_positions[i+1])
                print(f"    Matched across words: '{word_positions[i]['text']}' + '{word_positions[i+1]['text']}' contains target '{target_text}'")
                break
            
            # Try 3 words if available
            if i < len(word_positions) - 2:
                combined_text = word_positions[i]['text'] + word_positions[i+1]['text'] + word_positions[i+2]['text']
                combined_clean = re.sub(r'[^\w]', '', combined_text)
                
                if is_subsequence_match(target_clean_no_special, combined_clean):
                    matching_positions.append(word_positions[i])
                    matching_positions.append(word_positions[i+1])
                    matching_positions.append(word_positions[i+2])
                    print(f"    Matched across words: '{word_positions[i]['text']}' + '{word_positions[i+1]['text']}' + '{word_positions[i+2]['text']}' contains target '{target_text}'")
                    break
    
    return matching_positions

def mask_qr_code_and_barcode(img, positions):
    """
    Detect and mask QR codes and barcodes with improved detection.
    Tries multiple preprocessing techniques to improve detection accuracy.
    """
    detected_codes = []
    
    # Method 1: Try with grayscale (original implementation)
    img_gray = img.convert("L")
    decoded_objects = decode(img_gray)
    for obj in decoded_objects:
        x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
        detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "grayscale"})
    
    # If nothing found, try with color image (pyzbar sometimes works better with color)
    if not detected_codes:
        decoded_objects = decode(img)
        for obj in decoded_objects:
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "color"})
    
    # Method 2: Try with contrast enhancement
    if not detected_codes:
        enhancer = ImageEnhance.Contrast(img)
        img_contrast = enhancer.enhance(2.0)
        decoded_objects = decode(img_contrast)
        for obj in decoded_objects:
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "contrast"})
    
    # Method 3: Try with brightness adjustment
    if not detected_codes:
        enhancer = ImageEnhance.Brightness(img)
        img_bright = enhancer.enhance(1.5)
        decoded_objects = decode(img_bright)
        for obj in decoded_objects:
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "brightness"})
    
    # Method 4: Try with sharpening
    if not detected_codes:
        enhancer = ImageEnhance.Sharpness(img)
        img_sharp = enhancer.enhance(2.0)
        decoded_objects = decode(img_sharp)
        for obj in decoded_objects:
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "sharpness"})
    
    # Method 5: Try with 2x upscaling (helps with small QR codes)
    if not detected_codes:
        original_width, original_height = img.size
        img_large = img.resize((original_width * 2, original_height * 2), Image.LANCZOS)
        decoded_objects = decode(img_large)
        # Scale coordinates back to original size
        for obj in decoded_objects:
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            detected_codes.append({
                "x0": x // 2,
                "y0": y // 2,
                "x1": (x + w) // 2,
                "y1": (y + h) // 2,
                "method": "upscaled"
            })
    
    # Method 6: Try OpenCV QR detector as fallback
    if not detected_codes:
        try:
            # Convert PIL to OpenCV format
            img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            
            # Try QRCodeDetector
            qr_detector = cv2.QRCodeDetector()
            data, bbox, straight_qrcode = qr_detector.detectAndDecode(img_cv)
            
            if bbox is not None:
                # bbox is a numpy array of shape (1, 4, 2) containing corner points
                bbox = bbox[0]  # Get first detection
                x_coords = bbox[:, 0]
                y_coords = bbox[:, 1]
                
                x0 = int(np.min(x_coords))
                y0 = int(np.min(y_coords))
                x1 = int(np.max(x_coords))
                y1 = int(np.max(y_coords))
                
                detected_codes.append({
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "method": "opencv"
                })
        except Exception as e:
            print(f"    OpenCV QR detection failed: {e}")
    
    # Method 7: Try with binary thresholding
    if not detected_codes:
        try:
            img_cv = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
            # Apply adaptive thresholding
            img_thresh = cv2.adaptiveThreshold(
                img_cv, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                cv2.THRESH_BINARY, 11, 2
            )
            # Convert back to PIL for pyzbar
            img_thresh_pil = Image.fromarray(img_thresh)
            decoded_objects = decode(img_thresh_pil)
            for obj in decoded_objects:
                x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
                detected_codes.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h, "method": "threshold"})
        except Exception as e:
            print(f"    Threshold detection failed: {e}")
    
    # Add all detected codes to positions
    for code in detected_codes:
        print(f"    Found QR/barcode using method: {code['method']} at ({code['x0']}, {code['y0']}, {code['x1']}, {code['y1']})")
        positions.append({"x0": code["x0"], "y0": code["y0"], "x1": code["x1"], "y1": code["y1"]})
    
    if not detected_codes:
        print("    No QR codes or barcodes detected with any method")
    
    return positions

def clean_text(text):
    """Clean extracted text by removing unwanted characters."""
    return re.sub(r'\(cid:\d+\)', '', text)

def is_vertical(dir_vec):
    """Check if text direction vector indicates vertical text."""
    # dir = (dx, dy) unit vector for the line's advance direction
    dx, dy = dir_vec
    ang = math.degrees(math.atan2(dy, dx))  # 0°=left→right, 90°=bottom→top
    # normalize to [-180, 180]
    if ang > 180: ang -= 360
    return abs(abs(ang) - 90) <= ANGLE_TOLERANCE_DEG

def detect_text_with_pymupdf(pdf_bytes, target_text):
    """Detect both horizontal and vertical text using PyMuPDF's text analysis."""
    horizontal_positions = {}
    vertical_positions = {}
    
    # Create a temporary BytesIO object for PyMuPDF
    pdf_stream = BytesIO(pdf_bytes.getvalue())
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    for page_number, page in enumerate(doc, start=1):
        page_horizontal_positions = []
        page_vertical_positions = []
        
        # Get text as a structured dict so we can examine line directions & boxes
        text = page.get_text("dict")
        
        for block in text.get("blocks", []):
            if block.get("type", 0) != 0:  # 0 = text block
                continue
                
            for line in block.get("lines", []):
                dir_vec = line.get("dir", (1, 0))
                is_vertical_text = is_vertical(dir_vec)
                
                # Check if this line contains our target text
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                
                if target_text.lower() in line_text.lower():
                    # Union all span boxes in this line
                    rect = None
                    for span in line.get("spans", []):
                        r = fitz.Rect(span["bbox"])
                        rect = r if rect is None else rect | r

                    if rect is not None:
                        # Add padding and convert to our format
                        padded_rect = fitz.Rect(
                            rect.x0 - PADDING, 
                            rect.y0 - PADDING, 
                            rect.x1 + PADDING, 
                            rect.y1 + PADDING
                        )
                        
                        position_data = {
                            "x0": padded_rect.x0,
                            "y0": padded_rect.y0,
                            "x1": padded_rect.x1,
                            "y1": padded_rect.y1,
                            "text": line_text.strip(),
                            "is_vertical": is_vertical_text
                        }
                        
                        if is_vertical_text:
                            page_vertical_positions.append(position_data)
                        else:
                            page_horizontal_positions.append(position_data)
        
        if page_horizontal_positions:
            horizontal_positions[page_number] = page_horizontal_positions
        if page_vertical_positions:
            vertical_positions[page_number] = page_vertical_positions
    
    doc.close()
    return horizontal_positions, vertical_positions

def mask_text_in_image(image, target_text, image_format, original_url=None):
    """Mask text in image using AWS Textract for word detection, with blur and QR/barcode masking"""
    s3_key = None
    
    try:
        # Convert image to bytes for S3 upload
        image_buffer = BytesIO()
        image.save(image_buffer, format=image_format.upper())
        image_buffer.seek(0)
        
        # Upload to S3 for Textract
        upload_start = time.time()
        temp_url = original_url if original_url else f"temp_{time.time()}"
        s3_key = upload_image_to_s3_temp(image_buffer, temp_url)
        log_timing("Image upload to S3 for Textract", upload_start)
        
        # Call Textract
        textract_start = time.time()
        textract_response = call_textract_for_image(s3_key)
        log_timing("Textract API call", textract_start)
        
        # Parse response to get word positions
        parse_start = time.time()
        word_positions = parse_textract_response(textract_response, image)
        log_timing("Parse Textract response", parse_start)
        
        print(f"Found {len(word_positions)} words in image")
        
        # Search for target text
        target_positions = []
        if target_text:
            search_start = time.time()
            print(f"Searching for target text: '{target_text}'")
            print(f"Total words to search: {len(word_positions)}")
            # Debug: show first few words
            if word_positions:
                print(f"Sample words: {[w['text'] for w in word_positions[:10]]}")
            target_positions = search_target_text_in_words(word_positions, target_text)
            log_timing("Target text search", search_start)
            print(f"Found {len(target_positions)} matching words for target text: '{target_text}'")
        
        # Create a copy of the image for masking
        masked_image = image.copy()
        draw = ImageDraw.Draw(masked_image)
        
        # Apply blur to target text positions
        for pos in target_positions:
            x0, y0, x1, y1 = pos['x0'], pos['y0'], pos['x1'], pos['y1']
            print(f"  Blurring text: '{pos['text']}' at ({x0}, {y0}, {x1}, {y1})")
            crop_region = masked_image.crop((x0, y0, x1, y1))
            blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=15))
            masked_image.paste(blurred, (x0, y0))
        
        # Detect and mask QR codes and barcodes (ALWAYS applied)
        print("Detecting QR codes and barcodes...")
        qr_start_time = time.time()
        qr_barcode_positions = []
        mask_qr_code_and_barcode(masked_image, qr_barcode_positions)
        
        # Apply white fill for QR codes and barcodes
        for qr_pos in qr_barcode_positions:
            print(f"  Masking QR/barcode at ({qr_pos['x0']}, {qr_pos['y0']}, {qr_pos['x1']}, {qr_pos['y1']})")
            draw.rectangle([qr_pos["x0"], qr_pos["y0"], qr_pos["x1"], qr_pos["y1"]], fill="white")
        
        log_timing("QR code detection and masking", qr_start_time)
        
        # Convert masked image to bytes
        output_buffer = BytesIO()
        masked_image.save(output_buffer, format=image_format.upper())
        output_buffer.seek(0)
        
        return output_buffer.getvalue()
        
    finally:
        # Always cleanup temporary S3 file
        if s3_key:
            try:
                s3_client.delete_object(Bucket=BUCKET_NAME, Key=s3_key)
                print(f"Cleaned up temporary S3 file: {s3_key}")
            except Exception as e:
                print(f"Warning: Could not delete temporary S3 file {s3_key}: {e}")

def mask_text_in_pdf_pymupdf(pdf_bytes, target_text):
    """Fast and accurate masking using PyMuPDF for text detection with blur effects, plus QR code detection."""
    output_buffer = BytesIO()
    
    print(f"Detecting text using PyMuPDF: '{target_text}'")
    detection_start = time.time()
    
    try:
        # Detect both horizontal and vertical text using PyMuPDF
        horizontal_positions, vertical_positions = detect_text_with_pymupdf(pdf_bytes, target_text)
        
        log_timing("PyMuPDF text detection", detection_start)
        
        print(f"Found horizontal text on {len(horizontal_positions)} pages")
        print(f"Found vertical text on {len(vertical_positions)} pages")
    except Exception as e:
        print(f"PyMuPDF processing failed: {str(e)}")
        print("Falling back to Textract for PDF processing...")
        raise  # Re-raise to trigger fallback in lambda_handler
    
    # Create a temporary BytesIO object for PyMuPDF processing
    pdf_stream = BytesIO(pdf_bytes.getvalue())
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    # Process each page
    for page_number, page in enumerate(doc, start=1):
        print(f"Processing page {page_number}...")
        
        # Convert page to high-resolution image for blur processing
        mat = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
        img_data = mat.tobytes("png")
        img = Image.open(BytesIO(img_data))
        draw = ImageDraw.Draw(img)
        
        img_width, img_height = img.size
        pdf_width, pdf_height = page.rect.width, page.rect.height
        x_scale = img_width / pdf_width
        y_scale = img_height / pdf_height
        
        # Collect all positions for this page
        page_positions = []
        
        # Add horizontal positions
        if page_number in horizontal_positions:
            for pos in horizontal_positions[page_number]:
                page_positions.append({
                    "x0": pos["x0"],
                    "y0": pos["y0"],
                    "x1": pos["x1"],
                    "y1": pos["y1"],
                    "is_vertical": False,
                    "text": pos["text"]
                })
        
        # Add vertical positions
        if page_number in vertical_positions:
            for pos in vertical_positions[page_number]:
                page_positions.append({
                    "x0": pos["x0"],
                    "y0": pos["y0"],
                    "x1": pos["x1"],
                    "y1": pos["y1"],
                    "is_vertical": True,
                    "text": pos["text"]
                })
        
        # Apply blur effects for all detected text
        for pos in page_positions:
            # Scale PDF coordinates to image coordinates
            img_x0 = int(pos["x0"] * x_scale)
            img_y0 = int(pos["y0"] * y_scale)
            img_x1 = int(pos["x1"] * x_scale)
            img_y1 = int(pos["y1"] * y_scale)
            
            if pos["is_vertical"]:
                print(f"  Blurring vertical text: {pos['text']} at ({img_x0}, {img_y0}, {img_x1}, {img_y1})")
                # Use stronger blur for vertical text
                crop_region = img.crop((img_x0, img_y0, img_x1, img_y1))
                blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=20))
                img.paste(blurred, (img_x0, img_y0))
            else:
                print(f"  Blurring horizontal text: {pos['text']} at ({img_x0}, {img_y0}, {img_x1}, {img_y1})")
                # Use standard blur for horizontal text
                crop_region = img.crop((img_x0, img_y0, img_x1, img_y1))
                blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=15))
                img.paste(blurred, (img_x0, img_y0))
        
        # Detect and mask QR codes and barcodes
        print(f"  Detecting QR codes and barcodes on page {page_number}...")
        qr_start_time = time.time()
        
        # Detect QR codes and barcodes
        qr_barcode_positions = []
        mask_qr_code_and_barcode(img, qr_barcode_positions)
        
        # Apply white fill for QR codes and barcodes (keep them completely masked)
        for qr_pos in qr_barcode_positions:
            print(f"    Masking QR/barcode at ({qr_pos['x0']}, {qr_pos['y0']}, {qr_pos['x1']}, {qr_pos['y1']})")
            draw.rectangle([qr_pos["x0"], qr_pos["y0"], qr_pos["x1"], qr_pos["y1"]], fill="white")
        
        log_timing(f"QR code detection on page {page_number}", qr_start_time)
        
        # Convert the processed image back to PDF
        img_buffer = BytesIO()
        img.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        
        # Clear the existing page content and insert the processed image
        page.clean_contents()  # Remove existing content
        page.insert_image(fitz.Rect(0, 0, page.rect.width, page.rect.height), stream=img_buffer.getvalue())
    
    # Save the processed PDF
    doc.save(output_buffer, deflate=True, clean=True)
    doc.close()
    
    return output_buffer.getvalue()

def upload_to_s3(file_bytes, original_url, bucket_name, file_type='pdf', file_format='pdf'):
    """Upload masked file (PDF or image) to S3 with original URL as a tag"""
    try:
        # Generate a unique key based on the original URL
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        
        # Determine extension and content type
        if file_type == 'image':
            ext = file_format.lower()  # jpeg or png
            if ext == 'jpeg':
                ext = 'jpg'
            content_type = f'image/{ext}'
        else:
            ext = 'pdf'
            content_type = 'application/pdf'
        
        s3_key = f"masked_certificates/{url_hash}.{ext}"
        
        # Upload the file with tags
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
            Tagging=f'original_url={original_url}'
        )
        
        return s3_key
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '403':
            print(f"Permission denied uploading to S3 bucket: {bucket_name}")
            print("Please check IAM role permissions for the Lambda function")
            raise Exception("S3 upload denied. Check IAM permissions.")
        else:
            print(f"Error uploading to S3: {str(e)}")
            raise
    except Exception as e:
        print(f"Unexpected error uploading to S3: {str(e)}")
        raise

def get_from_s3(original_url, bucket_name, file_type='pdf', file_format='pdf'):
    """Check if a masked version exists in S3 and return presigned URL"""
    try:
        # Generate the same key as used in upload
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        
        # Determine extension
        if file_type == 'image':
            ext = file_format.lower()
            if ext == 'jpeg':
                ext = 'jpg'
        else:
            ext = 'pdf'
        
        # Try to find the file with the determined extension
        s3_key = f"masked_certificates/{url_hash}.{ext}"
        
        # Check if object exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            # Generate presigned URL that expires in 24 hours
            s3_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=86400  # 24 hours
            )
            return s3_url
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                # Try alternative extensions for backward compatibility
                if ext == 'pdf':
                    # Try jpg and png in case it was an image
                    for alt_ext in ['jpg', 'png']:
                        alt_key = f"masked_certificates/{url_hash}.{alt_ext}"
                        try:
                            s3_client.head_object(Bucket=bucket_name, Key=alt_key)
                            s3_url = s3_client.generate_presigned_url(
                                'get_object',
                                Params={'Bucket': bucket_name, 'Key': alt_key},
                                ExpiresIn=86400
                            )
                            return s3_url
                        except:
                            continue
                print(f"Object not found in S3: {s3_key}")
                return None
            elif error_code == '403':
                print(f"Permission denied accessing S3 bucket: {bucket_name}")
                print("Please check IAM role permissions for the Lambda function")
                raise Exception("S3 access denied. Check IAM permissions.")
            else:
                print(f"Error accessing S3: {str(e)}")
                raise
    except Exception as e:
        print(f"Unexpected error checking S3: {str(e)}")
        raise

### KEEP THIS FUNCTION  ###
def api_response(status_code, body):
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "OPTIONS,POST",
            "Access-Control-Allow-Headers": "Content-Type,Authorization"
        },
        'body': json.dumps(body)
    }

def lambda_handler(event, context):
    """
    AWS Lambda handler function with PyMuPDF-based text detection and AWS Textract for images.
    Expected event format:
    {
        "pdf_url": "https://example.com/document.pdf",
        "text_to_detect": "text to blur"
    }
    """
    print('======> event', event)

    http_method = event.get('requestContext', {}).get('http', {}).get('method', '')
    print('=====> http method ::::', http_method)

    # Check HTTP method
    if http_method == 'OPTIONS':
        # Return CORS headers for preflight request
        return api_response(200, {})
    
    if http_method == "POST":
        try:
            total_start_time = time.time()
            
            body = json.loads(event.get('body', '{}'))
            
            # Access pdf_url and text_to_detect (keeping name for backward compatibility)
            file_url = body.get('pdf_url')  # Can be PDF or image URL
            text_to_detect = body.get('text_to_detect')
            skip_cache = body.get('skip_cache', False)  # New parameter to skip cache
            
            # Detect file type
            file_type, file_format = detect_file_type(file_url)
            print(f"Detected file type: {file_type} ({file_format})")
            
            # Check S3 cache first (unless skip_cache is enabled)
            if not skip_cache:
                s3_check_start = time.time()
                print("Checking for existing masked version in S3...")
                existing_s3_url = get_from_s3(file_url, BUCKET_NAME, file_type, file_format)
                log_timing("S3 cache check", s3_check_start)
                
                if existing_s3_url:
                    print(f"Found existing masked version in S3: {existing_s3_url}")
                    log_timing("Total operation (cache hit)", total_start_time)
                    return api_response(200, {
                        's3_url': existing_s3_url
                    })
            else:
                print("Cache skipped: skip_cache parameter is enabled. Processing file fresh...")

            # Route based on file type
            if file_type == 'image':
                # Process image with Textract
                download_start = time.time()
                print("Downloading image...")
                image_bytes, image = download_image_from_url(file_url)
                log_timing("Image download", download_start)
                
                # Process image with Textract
                process_start = time.time()
                print("Processing image with AWS Textract...")
                processed_image = mask_text_in_image(image, text_to_detect, file_format, file_url)
                log_timing("Image processing", process_start)
                
                # Upload to S3
                upload_start = time.time()
                print("Uploading to S3...")
                s3_key = upload_to_s3(processed_image, file_url, BUCKET_NAME, file_type, file_format)
                log_timing("S3 upload", upload_start)
                
            else:
                # Process PDF
                download_start = time.time()
                print("Downloading PDF...")
                pdf_bytes = download_pdf_from_url(file_url)
                log_timing("PDF download", download_start)
                
                # Try PyMuPDF first
                process_start = time.time()
                print("Processing PDF with PyMuPDF...")
                try:
                    processed_file = mask_text_in_pdf_pymupdf(pdf_bytes, text_to_detect)
                    log_timing("PDF processing (PyMuPDF)", process_start)
                except Exception as pdf_error:
                    # Fallback to Textract: extract first page as image
                    print(f"PyMuPDF failed, using Textract fallback: {str(pdf_error)}")
                    pdf_stream = BytesIO(pdf_bytes.getvalue())
                    doc = fitz.open(stream=pdf_stream, filetype="pdf")
                    
                    if len(doc) == 0:
                        raise Exception("PDF has no pages")
                    
                    # Extract first page as image
                    page = doc[0]
                    mat = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_data = mat.tobytes("png")
                    image = Image.open(BytesIO(img_data))
                    doc.close()
                    
                    # Process with Textract
                    processed_image = mask_text_in_image(image, text_to_detect, 'png', file_url)
                    
                    # Convert masked image back to PDF using PyMuPDF
                    masked_img = Image.open(BytesIO(processed_image))
                    img_bytes = BytesIO()
                    masked_img.save(img_bytes, format='PNG')
                    img_bytes.seek(0)
                    
                    # Create new PDF with the masked image
                    pdf_doc = fitz.open()
                    pdf_page = pdf_doc.new_page(width=image.width, height=image.height)
                    pdf_page.insert_image(fitz.Rect(0, 0, image.width, image.height), stream=img_bytes.getvalue())
                    
                    pdf_output = BytesIO()
                    pdf_doc.save(pdf_output, deflate=True)
                    pdf_doc.close()
                    processed_file = pdf_output.getvalue()
                    
                    log_timing("PDF processing (Textract fallback)", process_start)
                
                # Upload to S3
                upload_start = time.time()
                print("Uploading to S3...")
                s3_key = upload_to_s3(processed_file, file_url, BUCKET_NAME, file_type, file_format)
                log_timing("S3 upload", upload_start)

            # Generate presigned URL
            url_start = time.time()
            s3_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': s3_key},
                ExpiresIn=86400  # 24 hours
            )
            log_timing("URL generation", url_start)

            print(f"Uploaded to S3. Access URL: {s3_url}")
            log_timing("Total operation (cache miss)", total_start_time)
            
            return api_response(200, {
                's3_url': s3_url
            })
            
        except Exception as e:
            import traceback; traceback.print_exc();
            return api_response(500, {
                'error': str(e)
            })
