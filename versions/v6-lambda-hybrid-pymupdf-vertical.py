import json
import base64
import requests
import pdfplumber
from pytesseract import pytesseract, Output
from PIL import ImageFilter, ImageDraw, Image
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
import fitz  # PyMuPDF for better vertical text detection

# Initialize S3 client outside the handler for better performance
s3_client = boto3.client('s3')

# Get bucket name from environment variable
BUCKET_NAME = os.environ.get('AWS_S3_BUCKET', 'masked-certificates-bucket')

# Vertical text detection parameters
ANGLE_TOLERANCE_DEG = 12        # how close to 90° we consider "vertical"
PADDING = 1.5                   # extra points to grow the redaction box

def log_timing(operation, start_time):
    """Log timing for operations"""
    end_time = time.time()
    duration = end_time - start_time
    print(f"[{datetime.now()}] {operation}: {duration:.2f} seconds")

def download_pdf_from_url(url):
    """Download PDF from URL and return bytes"""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF from URL. Status code: {response.status_code}")
    return BytesIO(response.content)

def mask_qr_code_and_barcode(img, positions):
    """Detect and mask QR codes and barcodes."""
    img = img.convert("L")  # Convert to grayscale for better detection
    decoded_objects = decode(img)
    
    for obj in decoded_objects:
        x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
        positions.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h})
    
    return positions

def is_vertical(dir_vec):
    """Check if text direction vector indicates vertical text."""
    # dir = (dx, dy) unit vector for the line's advance direction
    dx, dy = dir_vec
    ang = math.degrees(math.atan2(dy, dx))  # 0°=left→right, 90°=bottom→top
    # normalize to [-180, 180]
    if ang > 180: ang -= 360
    return abs(abs(ang) - 90) <= ANGLE_TOLERANCE_DEG

def detect_vertical_text_with_pymupdf(pdf_bytes, target_text):
    """Detect vertical text using PyMuPDF's text direction analysis."""
    vertical_positions = {}
    
    # Create a temporary BytesIO object for PyMuPDF
    pdf_stream = BytesIO(pdf_bytes.getvalue())
    doc = fitz.open(stream=pdf_stream, filetype="pdf")
    
    for page_number, page in enumerate(doc, start=1):
        page_vertical_positions = []
        
        # Get text as a structured dict so we can examine line directions & boxes
        text = page.get_text("dict")
        
        for block in text.get("blocks", []):
            if block.get("type", 0) != 0:  # 0 = text block
                continue
                
            for line in block.get("lines", []):
                dir_vec = line.get("dir", (1, 0))  # default horizontal
                if not is_vertical(dir_vec):
                    continue

                # Check if this vertical line contains our target text
                line_text = ""
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                
                if target_text.lower() in line_text.lower():
                    # Union all span boxes in this vertical line
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
                        
                        page_vertical_positions.append({
                            "x0": padded_rect.x0,
                            "y0": padded_rect.y0,
                            "x1": padded_rect.x1,
                            "y1": padded_rect.y1,
                            "is_vertical": True,
                            "is_pymupdf": True
                        })
        
        if page_vertical_positions:
            vertical_positions[page_number] = page_vertical_positions
    
    doc.close()
    return vertical_positions

def clean_text(text):
    """Clean extracted text by removing unwanted characters."""
    return re.sub(r'\(cid:\d+\)', '', text)





def text_based_detection(pdf_bytes, target_text):
    """Extracts text and determines if the target text exists."""
    text_positions = {}

    with pdfplumber.open(pdf_bytes) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text()
            if text:
                cleaned_text = clean_text(text)
                if target_text in cleaned_text:
                    print(f"Text-based detection successful on page {page_number}")
                    for word in page.extract_words():
                        cleaned_word = clean_text(word["text"])
                        if target_text.lower() in cleaned_word.lower():
                            if page_number not in text_positions:
                                text_positions[page_number] = []
                            text_positions[page_number].append({
                                "x0": word["x0"],
                                "y0": word["top"],
                                "x1": word["x1"],
                                "y1": word["bottom"]
                            })
    return text_positions if text_positions else None

def ocr_based_detection(image, target_text):
    """Perform OCR and return bounding boxes if text is found."""
    ocr_data = pytesseract.image_to_data(image, output_type=Output.DICT)
    positions = []

    for i, text in enumerate(ocr_data["text"]):
        cleaned_word = clean_text(text)
        if target_text in cleaned_word.strip():
            x, y, w, h = ocr_data["left"][i], ocr_data["top"][i], ocr_data["width"][i], ocr_data["height"][i]
            positions.append((x, y, x + w, y + h))

    return positions

def mask_text_in_pdf(pdf_bytes, target_text, dpi=300):
    """Enhanced masking that handles both horizontal and vertical text using PyMuPDF for accurate vertical detection."""
    output_buffer = BytesIO()

    text_positions = text_based_detection(pdf_bytes, target_text)

    # If text-based detection fails, use OCR-based detection
    ocr_required = text_positions is None

    # Detect vertical text using PyMuPDF (more accurate than OCR)
    print("Detecting vertical text using PyMuPDF...")
    pymupdf_vertical_positions = detect_vertical_text_with_pymupdf(pdf_bytes, target_text)
    print(f"PyMuPDF vertical positions: {pymupdf_vertical_positions}")

    with pdfplumber.open(pdf_bytes) as pdf:
        processed_pages = []

        for page_number, page in enumerate(pdf.pages, start=1):
            page_image = page.to_image(resolution=dpi)
            img = page_image.original.convert("RGB")
            draw = ImageDraw.Draw(img)
            
            img_width, img_height = img.size
            pdf_width, pdf_height = page.width, page.height
            x_scale = img_width / pdf_width
            y_scale = img_height / pdf_height

            # Always detect vertical text regardless of OCR requirement
            scaled_positions = []
            
            # Handle horizontal text detection
            if ocr_required:
                # Use OCR for horizontal text only
                normal_positions = ocr_based_detection(img, target_text)
                
                # Convert normal positions to dict format
                scaled_normal_positions = [{"x0": x, "y0": y, "x1": x1, "y1": y1, "is_vertical": False} 
                                         for x, y, x1, y1 in normal_positions]
                scaled_positions.extend(scaled_normal_positions)
            else:
                # Use text-based detection for horizontal text
                horizontal_positions = text_positions.get(page_number, [])
                scaled_horizontal_positions = [
                    {
                        "x0": int(box["x0"] * x_scale),
                        "y0": int(box["y0"] * y_scale),
                        "x1": int(box["x1"] * x_scale),
                        "y1": int(box["y1"] * y_scale),
                        "is_vertical": False
                    }
                    for box in horizontal_positions
                ]
                scaled_positions.extend(scaled_horizontal_positions)
            
            # Add PyMuPDF-detected vertical text positions (already in PDF coordinates)
            if page_number in pymupdf_vertical_positions:
                pymupdf_positions = pymupdf_vertical_positions[page_number]
                for pos in pymupdf_positions:
                    # Scale PyMuPDF positions to image coordinates
                    scaled_pos = {
                        "x0": int(pos["x0"] * x_scale),
                        "y0": int(pos["y0"] * y_scale),
                        "x1": int(pos["x1"] * x_scale),
                        "y1": int(pos["y1"] * y_scale),
                        "is_vertical": True,
                        "is_pymupdf": True
                    }
                    scaled_positions.append(scaled_pos)

            # Apply masking for detected text
            for box in scaled_positions:
                if box.get("is_vertical", False):
                    if box.get("is_pymupdf", False):
                        # For PyMuPDF-detected vertical text, use white fill (most accurate)
                        print(f"Masking PyMuPDF vertical text: {box}")
                        draw.rectangle([box["x0"], box["y0"], box["x1"], box["y1"]], fill="white")
                    else:
                        # For other vertical text, use blur
                        print(f"Masking vertical text: {box}")
                        crop_region = img.crop((box["x0"], box["y0"], box["x1"], box["y1"]))
                        blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=20))
                        img.paste(blurred, (box["x0"], box["y0"]))
                else:
                    # Normal horizontal text masking
                    crop_region = img.crop((box["x0"], box["y0"], box["x1"], box["y1"]))
                    blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=15))
                    img.paste(blurred, (box["x0"], box["y0"]))

            # Mask QR codes and barcodes
            qr_barcode_positions = []
            mask_qr_code_and_barcode(img, qr_barcode_positions)
            for box in qr_barcode_positions:
                draw.rectangle([box["x0"], box["y0"], box["x1"], box["y1"]], fill="white")

            processed_pages.append(img)

    if processed_pages:
        processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])

    return output_buffer.getvalue()

def upload_to_s3(pdf_bytes, original_url, bucket_name):
    """Upload masked PDF to S3 with original URL as a tag"""
    try:
        # Generate a unique key based on the original URL
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        s3_key = f"masked_certificates/{url_hash}.pdf"
        
        # Upload the file with tags
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType='application/pdf',
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

def get_from_s3(original_url, bucket_name):
    """Check if a masked version exists in S3 and return presigned URL"""
    try:
        # Generate the same key as used in upload
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        s3_key = f"masked_certificates/{url_hash}.pdf"
        
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
    AWS Lambda handler function.
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
            
            # Access pdf_url and text_to_detect
            pdf_url = body.get('pdf_url')
            text_to_detect = body.get('text_to_detect')
            
            # Check S3 cache first
            s3_check_start = time.time()
            print("Checking for existing masked version in S3...")
            existing_s3_url = get_from_s3(pdf_url, BUCKET_NAME)
            log_timing("S3 cache check", s3_check_start)
            
            if existing_s3_url:
                print(f"Found existing masked version in S3: {existing_s3_url}")
                log_timing("Total operation (cache hit)", total_start_time)
                return api_response(200, {
                    's3_url': existing_s3_url
                })

            # Download PDF
            download_start = time.time()
            print("Downloading PDF...")
            
            # Download PDF from URL
            pdf_bytes = download_pdf_from_url(pdf_url)
            log_timing("PDF download", download_start)
            # Process PDF
            process_start = time.time()
            print("Processing PDF...")
            processed_pdf = mask_text_in_pdf(pdf_bytes, text_to_detect)
            log_timing("PDF processing", process_start)
            
            # Upload to S3
            upload_start = time.time()
            print("Uploading to S3...")
            s3_key = upload_to_s3(processed_pdf, pdf_url, BUCKET_NAME)
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