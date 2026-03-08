#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Image Generator for Koto
Handles integration with Google Gemini's Imagen 3 / Image generation capabilities.
"""

import os
import base64
import time
from typing import Optional
from google import genai
from google.genai import types

class ImageGenerator:
    """
    Handles image generation requests using Gemini/Imagen.
    """
    
    def __init__(self, api_key: str = None):
        if not api_key:
            api_key = os.environ.get("GEMINI_API_KEY")
        
        self.client = None
        if api_key:
            self.client = genai.Client(api_key=api_key, http_options={'api_version': 'v1beta'})
        
        # Model preference: try specific image models first, then fallback to gemini-2.0-flash which might have image capability
        self.image_model = "imagen-4.0-generate-001" 
    
    def generate_image(self, prompt: str, output_path: str, aspect_ratio: str = "16:9") -> bool:
        """
        Generates an image from prompt and saves to output_path.
        
        Args:
            prompt: Description of image
            output_path: Local path to save the generated image (PNG/JPEG)
            aspect_ratio: "1:1", "3:4", "4:3", "9:16", "16:9"
        
        Returns:
            True if successful, False otherwise.
        """
        if not self.client:
            print("[ImageGenerator] No API Key available.")
            return False
            
        print(f"[ImageGenerator] Generating image for: '{prompt[:50]}...'")
        
        try:
            # Prepare configuration
            # Note: The exact parameter logic depends on the specific model version.
            # For Imagen 3 on Vertex AI / Gemini API, usually strictly typed config
            
            # Using standard generate_images API if available in the SDK version
            # Or generate_content with modalites
            
            # Let's try the modern genai.models.generate_images if it exists, or generate_content 
            # Check SDK capabilities by trial.
            
            # Scenario A: generate_images method
            try:
                response = self.client.models.generate_images(
                    model=self.image_model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio=aspect_ratio,
                        safety_filter_level="block_some",
                        person_generation="allow_adult"
                    )
                )
                if response.generated_images:
                    img_bytes = response.generated_images[0].image.image_bytes
                    with open(output_path, "wb") as f:
                        f.write(img_bytes)
                    print(f"[ImageGenerator] ✅ Saved to {output_path}")
                    return True
            except Exception as e_img:
                print(f"[ImageGenerator] generate_images failed: {e_img}, trying generate_content...")
                
                # Scenario B: generate_content (Gemini 3.1 Flash Image)
                # Some models support text-to-image via standard generate_content
                response = self.client.models.generate_content(
                    model="gemini-3.1-flash-image-preview", # Fallback to image model
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]
                    )
                )
                # Extract image from parts
                if response.candidates and response.candidates[0].content.parts:
                    for part in response.candidates[0].content.parts:
                        if part.inline_data:
                            img_data = base64.b64decode(part.inline_data.data)
                            with open(output_path, "wb") as f:
                                f.write(img_data)
                            print(f"[ImageGenerator] ✅ Saved to {output_path} (via generate_content)")
                            return True
                            
                print("[ImageGenerator] No image data found in response.")
                return False

        except Exception as e:
            print(f"[ImageGenerator] Critical Error: {e}")
            return False

    def generate_placeholder(self, prompt: str, output_path: str):
        """Generates a local placeholder image (solid color with text) if AI fails."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new('RGB', (1280, 720), color=(73, 109, 137))
            d = ImageDraw.Draw(img)
            # Draw text
            # Need a font, default to basic
            d.text((100, 360), f"Image Placeholder\n{prompt}", fill=(255, 255, 255))
            img.save(output_path)
        except ImportError:
            # Fallback if Pillow not installed (unlikely based on requirements)
            with open(output_path, 'wb') as f:
                f.write(b'') # Empty file
