import json
import base64
import pdfplumber
from pytesseract import pytesseract, Output
from PIL import ImageFilter, ImageDraw
from io import BytesIO
import requests
import os
import subprocess
from pyzbar.pyzbar import decode
import re

# # Configure Tesseract path (update for your local environment)
# os.environ["PATH"] += os.pathsep + "/opt/bin"
# os.environ["TESSDATA_PREFIX"] = "/opt/share/tessdata"
# os.environ["LD_LIBRARY_PATH"] = "/opt/lib:" + os.environ.get("LD_LIBRARY_PATH", "")
# pytesseract.tesseract_cmd = "/opt/bin/tesseract"


def download_pdf_from_url(url):
    """Download PDF from a URL and return bytes."""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF. Status code: {response.status_code}")
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

def apply_ocr(image, target_text):
    """Perform OCR on an image and detect the target text."""
    ocr_data = pytesseract.image_to_data(image, output_type=Output.DICT)
    # output_text = "tmp.txt"
    # ocr_data = subprocess.run(
    #         ["/opt/bin/tesseract", image, output_text],
    #         stdout=subprocess.PIPE,
    #         stderr=subprocess.PIPE
    #     )
    positions = []
    
    for i, text in enumerate(ocr_data["text"]):
        print(f"Text detected: {text}")
        cleaned_word = clean_text(text)
        
        if target_text in cleaned_word.strip():
            x, y, w, h = ocr_data["left"][i], ocr_data["top"][i], ocr_data["width"][i], ocr_data["height"][i]
            positions.append((x, y, x + w, y + h))
    
    return positions

def process_pdf(pdf_bytes, text_to_detect, dpi=300):
    """Process the PDF and return a blurred version as bytes."""
    output_buffer = BytesIO()
    processed_pages = []

    with pdfplumber.open(pdf_bytes) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            print(f"Processing page {page_number}...")

            # Handle image-based PDF
            page_image = page.to_image(resolution=dpi)
            img = page_image.original.convert("RGB")
            draw = ImageDraw.Draw(img)

            # Apply OCR
            positions = apply_ocr(img, text_to_detect)
            
            qr_barcode_positions = []
            mask_qr_code_and_barcode(img, qr_barcode_positions)

            # Blur OCR-detected regions
            for x0, y0, x1, y1 in positions:
                cropped_region = img.crop((x0, y0, x1, y1))
                blurred = cropped_region.filter(ImageFilter.GaussianBlur(radius=15))
                img.paste(blurred, (x0, y0))
                
            for box in qr_barcode_positions:
                print(f"Masking QR code or barcode at position: {box}")
                draw.rectangle([box["x0"], box["y0"], box["x1"], box["y1"]], fill="white")

            processed_pages.append(img)

    if processed_pages:
        processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])
    
    return output_buffer.getvalue()


def main():
    """Run the function locally with static inputs."""
    # Static inputs
    pdf_url = "https://s3.amazonaws.com/illuminex-media/uploads/diamond/gia_cert/6432087098.pdf"  # Replace with your PDF URL if needed
    text_to_detect = "6432087098"  # Text to detect and blur

    try:
        # Download the PDF
        print("Downloading PDF...")
        pdf_bytes = download_pdf_from_url(pdf_url)

        # Process the PDF
        print("Processing PDF...")
        processed_pdf = process_pdf(pdf_bytes, text_to_detect)

        # Save the processed PDF to a file
        output_file = "6432087098_processed_output.pdf"
        with open(output_file, "wb") as f:
            f.write(processed_pdf)

        print(f"Processed PDF saved as {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Run the script locally
if __name__ == "__main__":
    main()

