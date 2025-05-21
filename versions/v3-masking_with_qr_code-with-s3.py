import json
import base64
import requests
import pdfplumber
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

def configure_aws_credentials(aws_access_key_id=None, aws_secret_access_key=None, region_name='us-east-1'):
    """Configure AWS credentials"""
    if aws_access_key_id and aws_secret_access_key:
        os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key_id
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_access_key
        os.environ['AWS_DEFAULT_REGION'] = region_name

def download_pdf_from_url(url):
    """Download PDF from URL and return bytes"""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF from URL. Status code: {response.status_code}")
    return BytesIO(response.content)

def mask_qr_code_and_barcode(img, positions):
    """Mask QR codes and barcodes on the image."""
    try:
        # Ensure the image is in the correct format
        img = img.convert("L")  # Convert to grayscale for better detection

        # Decode QR codes and barcodes
        decoded_objects = decode(img)
        if not decoded_objects:
            print("No QR codes or barcodes detected.")

        # Log detected objects
        for obj in decoded_objects:
            print(f"Detected object: {obj}")
            x, y, w, h = obj.rect.left, obj.rect.top, obj.rect.width, obj.rect.height
            positions.append({"x0": x, "y0": y, "x1": x + w, "y1": y + h})

        return positions
    except Exception as e:
        print(f"Error in mask_qr_code_and_barcode: {e}")
        return positions
    
def clean_text(text):
    return re.sub(r'\(cid:\d+\)', '', text)

def process_pdf(pdf_bytes, texts_to_detect, dpi=300):
    """Process PDF and return bytes of processed PDF"""
    output_buffer = BytesIO()
    text_positions = {}

    if isinstance(texts_to_detect, str):
        texts_to_detect = [texts_to_detect]

    texts_to_detect = [text.lower() for text in texts_to_detect]  # Convert to lowercase for case-insensitive matching

    with pdfplumber.open(pdf_bytes) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            
            text = page.extract_text()
            if text:
                cleaned_text = clean_text(text)
                for detect_text in texts_to_detect:
                    matches = re.finditer(re.escape(detect_text), cleaned_text, re.IGNORECASE)
                    for match in matches:
                        for word in page.extract_words():
                            print(word['text'])
                            cleaned_word = clean_text(word["text"])
                            print(cleaned_word)
                            if detect_text.lower() in cleaned_word.lower():
                                if page_number not in text_positions:
                                    text_positions[page_number] = []
                                text_positions[page_number].append({
                                    "x0": word["x0"],
                                    "y0": word["top"],
                                    "x1": word["x1"],
                                    "y1": word["bottom"]
                                })


    processed_pages = []
    with pdfplumber.open(pdf_bytes) as pdf_doc:
        for page_number, page in enumerate(pdf_doc.pages, start=1):
            page_image = page.to_image(resolution=dpi)
            img = page_image.original.convert("RGB")
            draw = ImageDraw.Draw(img)

            img_width, img_height = img.size
            pdf_width, pdf_height = page.width, page.height

            x_scale = img_width / pdf_width
            y_scale = img_height / pdf_height

            # Detect and mask QR codes or barcodes
            qr_barcode_positions = []
            mask_qr_code_and_barcode(img, qr_barcode_positions)

            # Scale positions and add to text positions
            all_positions = text_positions.get(page_number, [])
            for box in all_positions:
                x0_img = int(box["x0"] * x_scale)
                y0_img = int(box["y0"] * y_scale)
                x1_img = int(box["x1"] * x_scale)
                y1_img = int(box["y1"] * y_scale)

                crop_region = img.crop((x0_img, y0_img, x1_img, y1_img))
                blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=15))
                img.paste(blurred, (x0_img, y0_img))
                # draw.rectangle([x0_img, y0_img, x1_img, y1_img], fill="white")

            for box in qr_barcode_positions:
                print(f"Masking QR code or barcode at position: {box}")
                draw.rectangle([box["x0"], box["y0"], box["x1"], box["y1"]], fill="white")

            processed_pages.append(img)

    if processed_pages:
        processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])

    return output_buffer.getvalue()

def upload_to_s3(pdf_bytes, original_url, bucket_name="your-bucket-name"):
    """
    Upload masked PDF to S3 with original URL as a tag
    Returns the S3 object key
    """
    try:
        s3_client = boto3.client('s3')
        
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

def get_from_s3(original_url, bucket_name="your-bucket-name"):
    """
    Check if a masked version of the PDF already exists in S3
    Returns the S3 URL if found, None otherwise
    """
    try:
        s3_client = boto3.client('s3')
        
        # Generate the same key as used in upload
        url_hash = hashlib.md5(original_url.encode()).hexdigest()
        s3_key = f"masked_certificates/{url_hash}.pdf"
        
        # Check if object exists
        try:
            s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            s3_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=86400
            )
            return s3_url
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return None
            raise
    except ClientError as e:
        print(f"Error checking S3: {e}")
        raise

def log_timing(operation, start_time):
    """Log timing for operations"""
    end_time = time.time()
    duration = end_time - start_time
    print(f"[{datetime.now()}] {operation}: {duration:.2f} seconds")

def main():
    """Run the function locally with static inputs."""
    total_start_time = time.time()
    
    # Static inputs
    # pdf_url = "https://certimage.s3-accelerate.amazonaws.com/images/full_size/certificates/6173606887.pdf"
    # text_to_detect = "6173606887"
    pdf_url = "https://s3.amazonaws.com/lgdcertificates/LG528232999.pdf"
    text_to_detect = "LG528232999"
    bucket_name = "masked-certificates-bucket"

    try:
        # Check S3 cache
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
        processed_pdf = process_pdf(pdf_bytes, text_to_detect)
        log_timing("PDF processing", process_start)

        # Upload to S3
        upload_start = time.time()
        print("Uploading to S3...")
        s3_key = upload_to_s3(processed_pdf, pdf_url, bucket_name)
        log_timing("S3 upload", upload_start)
        
        # Generate URL
        url_start = time.time()
        s3_client = boto3.client('s3')
        s3_url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': s3_key},
            ExpiresIn=3600
        )
        log_timing("URL generation", url_start)
        
        print(f"Uploaded to S3. Access URL: {s3_url}")
        log_timing("Total operation (cache miss)", total_start_time)
        return s3_url

    except Exception as e:
        print(f"An error occurred: {e}")
        log_timing("Failed operation", total_start_time)
        raise

# Run the script locally
if __name__ == "__main__":
    configure_aws_credentials(
        aws_access_key_id='***REMOVED-AWS-ACCESS-KEY-ID***',
        aws_secret_access_key='***REMOVED-AWS-SECRET-ACCESS-KEY***',
        region_name='us-east-1'
    )
    main()
