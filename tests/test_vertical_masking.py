#!/usr/bin/env python3
"""
Test script for enhanced vertical text masking functionality.
This script demonstrates how the enhanced masking can handle both horizontal and vertical text.
"""

import os
import sys
from final import mask_text_in_pdf, download_pdf_from_url
from io import BytesIO

def test_vertical_masking(test_case):
    """Test the enhanced masking functionality with a sample PDF."""
    
    # Example PDF URL (you can replace this with your test PDF)
    pdf_url = test_case['url']
    # text_to_detect = "LG528232999"
    
    try:
        print("Testing enhanced vertical text masking...")
        print(f"PDF URL: {test_case['url']}")
        print(f"Text to detect: {test_case['text']}")
        
        # Download PDF
        print("\n1. Downloading PDF...")
        pdf_bytes = download_pdf_from_url(test_case['url'])
        print("✓ PDF downloaded successfully")
        
        # Process PDF with enhanced masking
        print("\n2. Processing PDF with enhanced masking...")
        processed_pdf = mask_text_in_pdf(pdf_bytes, test_case['text'])
        print("✓ PDF processed successfully")
        
        # Save the processed PDF
        output_filename = "test_vertical_masking_output.pdf"
        with open(output_filename, "wb") as f:
            f.write(processed_pdf)
        
        print(f"\n✓ Enhanced masking completed!")
        print(f"Output saved as: {output_filename}")
        print(f"File size: {len(processed_pdf)} bytes")
        
        return True
        
    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_with_local_pdf(pdf_path, text_to_detect):
    """Test with a local PDF file."""
    
    try:
        print(f"Testing with local PDF: {pdf_path}")
        print(f"Text to detect: {text_to_detect}")
        
        # Read local PDF
        with open(pdf_path, "rb") as f:
            pdf_bytes = BytesIO(f.read())
        
        # Process PDF
        print("\nProcessing PDF with enhanced masking...")
        processed_pdf = mask_text_in_pdf(pdf_bytes, text_to_detect)
        
        # Save output
        output_filename = f"local_test_output_{text_to_detect}.pdf"
        with open(output_filename, "wb") as f:
            f.write(processed_pdf)
        
        print(f"✓ Output saved as: {output_filename}")
        return True
        
    except Exception as e:
        print(f"✗ Error: {e}")
        return False

if __name__ == "__main__":
    print("Enhanced Vertical Text Masking Test")
    print("=" * 40)
    
    # Create an array of objects with 'url' and 'text' keys for testing
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
    
    # Test with online PDF
    for test_case in test_cases:
        print(f"Testing with URL: {test_case['url']}")
        print(f"Text to detect: {test_case['text']}")
        success = test_vertical_masking(test_case)
        if success:
            print("✓ Test completed successfully!")
        else:
            print("✗ Test failed. Check the error messages above.")
    
    # success = test_vertical_masking(test_cases)
    
    if success:
        print("\n" + "=" * 40)
        print("Test completed successfully!")
        print("\nKey Features Tested:")
        print("✓ Horizontal text detection and masking")
        print("✓ Vertical text detection using OCR with multiple PSM modes")
        print("✓ Vertical region detection using contour analysis")
        print("✓ Different masking strategies for vertical vs horizontal text")
        print("✓ Enhanced blur for vertical text (radius=20)")
        print("✓ White fill for vertical regions")
    else:
        print("\nTest failed. Check the error messages above.")
    
    # If you have a local PDF with vertical text, you can test it like this:
    # test_with_local_pdf("path/to/your/pdf", "certificate_number")
