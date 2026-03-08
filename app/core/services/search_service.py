#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Try importing google.genai (v2 SDK)
try:
    from google import genai
    from google.genai import types
    HAS_GENAI_V2 = True
except ImportError:
    HAS_GENAI_V2 = False

class SearchService:
    """
    Search service using Google GenAI (v2) Search Grounding.
    Wrapper to perform a search query and return a grounded answer or search snippets.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self.client = None
        if HAS_GENAI_V2 and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                logger.error(f"Failed to initialize GenAI v2 client: {e}")

    def search(self, query: str) -> Dict[str, Any]:
        """
        Perform a search.
        Since we use Google Search Grounding, this actually asks an LLM to answer based on search.
        
        Returns:
            {
                "success": bool,
                "data": str (the answer),
                "metadata": dict (grounding info)
            }
        """
        if not HAS_GENAI_V2:
            return {"success": False, "error": "google.genai package not installed."}
        
        if not self.client:
             return {"success": False, "error": "Search client not initialized (missing API key?)"}
             
        try:
            # We use a model to synthesize the search results.
            # This is how 'web_searcher.py' did it.
            response = self.client.models.generate_content(
                model="gemini-2.5-flash", # Use a fast model
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    system_instruction="You are a search assistant. Provide precise information from search results."
                )
            )
            
            if response.text:
                return {
                    "success": True,
                    "data": response.text,
                    # We could extract grounding chunks if needed
                    "metadata": {"grounding_chunks": str(response.candidates[0].grounding_metadata) if response.candidates else ""}
                }
            else:
                 return {"success": False, "error": "No response text generated from search."}
                 
        except Exception as e:
            return {"success": False, "error": f"Search failed: {str(e)}"}
