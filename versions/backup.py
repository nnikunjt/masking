import json
import base64
import requests
import pdfplumber
from PIL import ImageFilter
from io import BytesIO

def download_pdf_from_url(url):
    """Download PDF from URL and return bytes"""
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to download PDF from URL. Status code: {response.status_code}")
    return BytesIO(response.content)

def process_pdf(pdf_bytes, text_to_detect, dpi=300):
    """Process PDF and return bytes of processed PDF"""
    output_buffer = BytesIO()
    positions = {}
    
    with pdfplumber.open(pdf_bytes) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words()
            for word in words:
                if text_to_detect in word["text"]:
                    if page_number not in positions:
                        positions[page_number] = []
                    positions[page_number].append({
                        "x0": word["x0"],
                        "y0": word["top"],
                        "x1": word["x1"],
                        "y1": word["bottom"]
                    })
    print("Possitions:::: ", positions)
    processed_pages = []
    with pdfplumber.open(pdf_bytes) as pdf_doc:
        for page_number, page in enumerate(pdf_doc.pages, start=1):
            page_image = page.to_image(resolution=dpi)
            img = page_image.original.convert("RGB")
            
            img_width, img_height = img.size
            pdf_width, pdf_height = page.width, page.height
            
            x_scale = img_width / pdf_width
            y_scale = img_height / pdf_height
            
            if page_number in positions:
                for box in positions[page_number]:
                    x0_img = int(box["x0"] * x_scale)
                    y0_img = int(box["y0"] * y_scale)
                    x1_img = int(box["x1"] * x_scale)
                    y1_img = int(box["y1"] * y_scale)
                    
                    crop_region = img.crop((x0_img, y0_img, x1_img, y1_img))
                    blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=5))
                    img.paste(blurred, (x0_img, y0_img))
            
            processed_pages.append(img)
    
    if processed_pages:
        processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])
    
    return output_buffer.getvalue()

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
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",  # Or specify allowed origins
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",  # Allowed HTTP methods
                "Access-Control-Allow-Headers": "Content-Type, Authorization",  # Allowed headers
            },
            "body": ""
        }
    
    if http_method == "POST":
        try:

            body = json.loads(event.get('body', '{}'))
            
            # Access pdf_url and text_to_detect
            pdf_url = body.get('pdf_url')
            text_to_detect = body.get('text_to_detect')

            # pdf_url = event['pdf_url']
            # text_to_detect = event['text_to_detect']
            
            # Download PDF from URL
            pdf_bytes = download_pdf_from_url(pdf_url)
            
            # Process the PDF
            processed_pdf = process_pdf(pdf_bytes, text_to_detect)
            
            # Encode processed PDF as base64
            encoded_pdf = base64.b64encode(processed_pdf).decode('utf-8')
            
            return {
                'statusCode': 200,
                'headers': {
                    'Content-Type': 'application/json',
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "OPTIONS,POST",
                    "Access-Control-Allow-Headers": "Content-Type,Authorization"
                },
                'body': json.dumps({
                    'pdf_base64': encoded_pdf
                })
            }
            
        except Exception as e:
            import traceback; traceback.print_exc();
            return {
                'statusCode': 500,
                'headers': {
                    'Content-Type': 'application/json',
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "OPTIONS,POST",
                    "Access-Control-Allow-Headers": "Content-Type,Authorization"
                },
                'body': json.dumps({
                    'error': str(e)
                })
            }
            
            
            # import json
# import base64
# import requests
# import pdfplumber
# from PIL import ImageFilter
# from io import BytesIO

# def download_pdf_from_url(url):
#     """Download PDF from URL and return bytes"""
#     response = requests.get(url)
#     if response.status_code != 200:
#         raise Exception(f"Failed to download PDF from URL. Status code: {response.status_code}")
#     return BytesIO(response.content)

# def process_pdf(pdf_bytes, text_to_detect, dpi=300):
#     """Process PDF and return bytes of processed PDF"""
#     output_buffer = BytesIO()
#     positions = {}
    
#     with pdfplumber.open(pdf_bytes) as pdf:
#         for page_number, page in enumerate(pdf.pages, start=1):
#             words = page.extract_words()
#             for word in words:
#                 if text_to_detect in word["text"]:
#                     if page_number not in positions:
#                         positions[page_number] = []
#                     positions[page_number].append({
#                         "x0": word["x0"],
#                         "y0": word["top"],
#                         "x1": word["x1"],
#                         "y1": word["bottom"]
#                     })
    
#     processed_pages = []
#     with pdfplumber.open(pdf_bytes) as pdf_doc:
#         for page_number, page in enumerate(pdf_doc.pages, start=1):
#             page_image = page.to_image(resolution=dpi)
#             img = page_image.original.convert("RGB")
            
#             img_width, img_height = img.size
#             pdf_width, pdf_height = page.width, page.height
            
#             x_scale = img_width / pdf_width
#             y_scale = img_height / pdf_height
            
#             if page_number in positions:
#                 for box in positions[page_number]:
#                     x0_img = int(box["x0"] * x_scale)
#                     y0_img = int(box["y0"] * y_scale)
#                     x1_img = int(box["x1"] * x_scale)
#                     y1_img = int(box["y1"] * y_scale)
                    
#                     crop_region = img.crop((x0_img, y0_img, x1_img, y1_img))
#                     blurred = crop_region.filter(ImageFilter.GaussianBlur(radius=5))
#                     img.paste(blurred, (x0_img, y0_img))
            
#             processed_pages.append(img)
    
#     if processed_pages:
#         processed_pages[0].save(output_buffer, format='PDF', save_all=True, append_images=processed_pages[1:])
    
#     return output_buffer.getvalue()

# def lambda_handler(event, context):
#     """
#     AWS Lambda handler function.
#     Expected event format:
#     {
#         "pdf_url": "https://example.com/document.pdf",
#         "text_to_detect": "text to blur"
#     }
#     """
#     try:
#         pdf_url = event['pdf_url']
#         text_to_detect = event['text_to_detect']
        
#         # Download PDF from URL
#         pdf_bytes = download_pdf_from_url(pdf_url)
        
#         # Process the PDF
#         processed_pdf = process_pdf(pdf_bytes, text_to_detect)
        
#         # Encode processed PDF as base64
#         encoded_pdf = base64.b64encode(processed_pdf).decode('utf-8')
        
#         return {
#             'statusCode': 200,
#             'headers': {
#                 'Content-Type': 'application/json'
#             },
#             'body': json.dumps({
#                 'pdf_base64': encoded_pdf
#             })
#         }
        
#     except Exception as e:
#         import traceback; traceback.print_exc();
#         return {
#             'statusCode': 500,
#             'body': json.dumps({
#                 'error': str(e)
#             })
#         }
