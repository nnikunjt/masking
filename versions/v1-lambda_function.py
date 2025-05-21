import json
import base64
import pdfplumber
from pytesseract import pytesseract, Output
from PIL import Image, ImageFilter
from io import BytesIO
import requests


def download_pdf_from_url(url):
    """Download PDF from a URL and return bytes."""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF. Status code: {response.status_code}")
    return BytesIO(response.content)

def is_text_based(page):
    """Check if a PDF page contains text."""
    return bool(page.extract_text())

def apply_ocr(image, target_text):
    """Perform OCR on an image and detect the target text."""
    ocr_data = pytesseract.image_to_data(image, output_type=Output.DICT)
    positions = []
    
    for i, text in enumerate(ocr_data["text"]):
        if text.strip() == target_text:
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

            if not is_text_based(page):
                print('====> is text based pdf')
                # Handle text-based PDF
                words = page.extract_words()
                positions = []

                for word in words:
                    if text_to_detect in word["text"]:
                        positions.append({
                            "x0": word["x0"],
                            "y0": word["top"],
                            "x1": word["x1"],
                            "y1": word["bottom"]
                        })
                
                page_image = page.to_image(resolution=dpi)
                img = page_image.original.convert("RGB")

                # Scale text positions to image coordinates
                img_width, img_height = img.size
                pdf_width, pdf_height = page.width, page.height
                x_scale = img_width / pdf_width
                y_scale = img_height / pdf_height

                for box in positions:
                    x0_img = int(box["x0"] * x_scale)
                    y0_img = int(box["y0"] * y_scale)
                    x1_img = int(box["x1"] * x_scale)
                    y1_img = int(box["y1"] * y_scale)

                    cropped_region = img.crop((x0_img, y0_img, x1_img, y1_img))
                    blurred = cropped_region.filter(ImageFilter.GaussianBlur(radius=5))
                    img.paste(blurred, (x0_img, y0_img))

            else:
                # Handle image-based PDF
                page_image = page.to_image(resolution=dpi)
                img = page_image.original.convert("RGB")

                # Apply OCR
                positions = apply_ocr(img, text_to_detect)

                # Blur OCR-detected regions
                for x0, y0, x1, y1 in positions:
                    cropped_region = img.crop((x0, y0, x1, y1))
                    blurred = cropped_region.filter(ImageFilter.GaussianBlur(radius=10))
                    img.paste(blurred, (x0, y0))

            processed_pages.append(img)

    if processed_pages:
        processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])
    
    return output_buffer.getvalue()

def main():
    """Run the function locally with static inputs."""
    # Static inputs
    pdf_url = "https://s3.amazonaws.com/lgdcertificates/LG528232999.pdf"  # Replace with your PDF URL
    text_to_detect = "LG528232999"  # Replace with the text to detect and blur

    try:
        # Download the PDF
        print("Downloading PDF...")
        pdf_bytes = download_pdf_from_url(pdf_url)

        # Process the PDF
        print("Processing PDF...")
        processed_pdf = process_pdf(pdf_bytes, text_to_detect)

        # Save the processed PDF to a file
        output_file = "processed_output-3.pdf"
        with open(output_file, "wb") as f:
            f.write(processed_pdf)

        print(f"Processed PDF saved as {output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

# Run the script locally
if __name__ == "__main__":
    main()

