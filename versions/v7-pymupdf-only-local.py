import json
import base64
import requests
import fitz  # PyMuPDF for efficient text detection
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

# Initialize S3 client outside the handler for better performance
s3_client = boto3.client('s3')

# Text detection parameters
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
                dir_vec = line.get("dir", (1, 0))  # default horizontal
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

def mask_text_in_pdf_pymupdf(pdf_bytes, target_text, dpi=300):
    """Fast and accurate masking using PyMuPDF for text detection with blur effects, plus QR code detection."""
    output_buffer = BytesIO()
    
    print(f"Detecting text using PyMuPDF: '{target_text}'")
    detection_start = time.time()
    
    # Detect both horizontal and vertical text using PyMuPDF
    horizontal_positions, vertical_positions = detect_text_with_pymupdf(pdf_bytes, target_text)
    
    log_timing("PyMuPDF text detection", detection_start)
    
    print(f"Found horizontal text on {len(horizontal_positions)} pages")
    print(f"Found vertical text on {len(vertical_positions)} pages")
    
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
        print(f"Error uploading to S3: {e}")
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
            if e.response['Error']['Code'] == '404':
                return None
            raise
    except ClientError as e:
        print(f"Error checking S3: {e}")
        raise

def main():
    """Run the function locally with static inputs."""
    total_start_time = time.time()
    
    # Example inputs
    test_cases = [
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/581334780.pdf",
            "text": "581334780"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/647425823.pdf",
            "text": "647425823"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/634421388.pdf",
            "text": "634421388"
        },
        {
            "url": "https://dtol-cert-images.s3.amazonaws.com/IGI_pdf/649405307.pdf",
            "text": "649405307"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/LG571320946.pdf",
            "text": "LG571320946"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/LG581345287.pdf",
            "text": "LG581345287"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/LG520206548.pdf",
            "text": "LG520206548"
        },
        {
            "url": "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/635497429.pdf",
            "text": "635497429"
        }
    ]
    
    # pdf_url = "https://s3.amazonaws.com/illuminex-media/uploads/diamond/igi_cert/647425823.pdf"
    # text_to_detect = "647425823"
    bucket_name = "masked-certificates-bucket"

    try:
        # Check S3 cache first
        for test_case in test_cases:
            s3_check_start = time.time()
            print("Checking for existing masked version in S3...")
            existing_s3_url = get_from_s3(test_case['url'], bucket_name)
            log_timing("S3 cache check", s3_check_start)
            
        # if existing_s3_url:
        #     print(f"Found existing masked version in S3: {existing_s3_url}")
        #     log_timing("Total operation (cache hit)", total_start_time)
        #     return existing_s3_url

        # Download PDF
            download_start = time.time()
            print("Downloading PDF...")
            pdf_bytes = download_pdf_from_url(test_case['url'])
            log_timing("PDF download", download_start)

            # Process PDF with PyMuPDF
            process_start = time.time()
            print("Processing PDF with PyMuPDF...")
            processed_pdf = mask_text_in_pdf_pymupdf(pdf_bytes, test_case['text'])
            log_timing("PDF processing", process_start)

            # Upload to S3
            upload_start = time.time()
            print("Uploading to S3...")
            s3_key = upload_to_s3(processed_pdf, test_case['url'], bucket_name)
            log_timing("S3 upload", upload_start)

            # Generate presigned URL
            url_start = time.time()
            s3_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=86400  # 24 hours
            )
            log_timing("URL generation", url_start)

            print(f"Uploaded to S3. Access URL: {s3_url}")
            log_timing("Total operation (cache miss)", total_start_time)
            # return s3_url

    except Exception as e:
        print(f"An error occurred: {e}")
        log_timing("Failed operation", total_start_time)
        raise

# Run locally
if __name__ == "__main__":
    main()
