import json
import base64
import requests
import pdfplumber
from pytesseract import pytesseract, Output
from PIL import ImageFilter, ImageDraw
from io import BytesIO
from pyzbar.pyzbar import decode
import re
import boto3
from botocore.exceptions import ClientError
import hashlib
import os
import time
from datetime import datetime

# Initialize S3 client outside the handler for better performance
s3_client = boto3.client('s3')

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
    """First tries text-based detection, then OCR if necessary."""
    output_buffer = BytesIO()

    text_positions = text_based_detection(pdf_bytes, target_text)

    # 2. **If text-based detection fails, use OCR-based detection**
    ocr_required = text_positions is None

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

            # **If OCR is required, detect text positions**
            if ocr_required:
                positions = ocr_based_detection(img, target_text)
                scaled_positions = [{"x0": x, "y0": y, "x1": x1, "y1": y1} for x, y, x1, y1 in positions]
            else:
                scaled_positions = text_positions.get(page_number, [])
                scaled_positions = [
                    {
                        "x0": int(box["x0"] * x_scale),
                        "y0": int(box["y0"] * y_scale),
                        "x1": int(box["x1"] * x_scale),
                        "y1": int(box["y1"] * y_scale),
                    }
                    for box in scaled_positions
                ]

            # **Apply masking for detected text**
            for box in scaled_positions:
                crop_region = img.crop((box["x0"], box["y0"], box["x1"], box["y1"]))
                blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=15))
                img.paste(blurred, (box["x0"], box["y0"]))

            # **Mask QR codes and barcodes**
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
    pdf_url = "https://s3.amazonaws.com/lgdcertificates/LG528232999.pdf"
    text_to_detect = "LG528232999"
    bucket_name = "masked-certificates-bucket"

    try:
        # Check S3 cache first
        s3_check_start = time.time()
        print("Checking for existing masked version in S3...")
        existing_s3_url = get_from_s3(pdf_url, bucket_name)
        log_timing("S3 cache check", s3_check_start)
        
        if existing_s3_url:
            print(f"Found existing masked version in S3: {existing_s3_url}")
            log_timing("Total operation (cache hit)", total_start_time)
            return existing_s3_url

        # Download PDF
        download_start = time.time()
        print("Downloading PDF...")
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
        s3_key = upload_to_s3(processed_pdf, pdf_url, bucket_name)
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
        return s3_url

    except Exception as e:
        print(f"An error occurred: {e}")
        log_timing("Failed operation", total_start_time)
        raise

# Run locally
if __name__ == "__main__":
    main()