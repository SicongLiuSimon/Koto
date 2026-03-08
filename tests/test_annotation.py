import os
import sys
import asyncio
import time
from dotenv import load_dotenv

# Add current directory to path (project root)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Load environment variables
load_dotenv(os.path.join(current_dir, 'config', 'gemini_config.env'))

try:
    from web.app import get_client
except ImportError:
    print("Failed to import web.app")
    sys.exit(1)

from web.document_feedback import DocumentFeedbackSystem

def run_test():
    print("Initializing AI client...")
    client = get_client()
    
    print("Initializing DocumentFeedbackSystem...")
    system = DocumentFeedbackSystem(gemini_client=client)
    
    file_path = os.path.join(current_dir, "web", "uploads", "电影时间的计算解析：基于大视觉语言模型的电影连续性研究.docx")
    user_requirement = "把所有不合适的翻译 不符合中文语序逻辑 生硬的地方改善"
    
    print(f"Testing file: {file_path}")
    print(f"Requirement: {user_requirement}")
    
    if not os.path.exists(file_path):
        print(f"ERROR: File not found at {file_path}")
        return
        
    print("\nStarting full_annotation_loop_streaming...")
    try:
        for event in system.full_annotation_loop_streaming(file_path, user_requirement):
            stage = event.get('stage', 'unknown')
            progress = event.get('progress', 0)
            message = event.get('message', '')
            detail = event.get('detail', '')
            
            print(f"[{progress}%] [{stage}] {message} | {detail}")
            
            if stage == 'complete':
                result = event.get('result', {})
                print("\n=== FINAL RESULT ===")
                print(f"Success: {result.get('success')}")
                print(f"Applied: {result.get('applied')}")
                print(f"Failed: {result.get('failed')}")
                print(f"Total: {result.get('total')}")
                print(f"Revised File: {result.get('revised_file')}")
                
            if stage == 'error':
                print(f"\n=== ERROR ===")
                print(f"Message: {message}")
                
    except Exception as e:
        print(f"\n=== EXCEPTION CAUGHT ===")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()
