#!/usr/bin/env python3
"""Quick test with the already-converted docx to verify E2E pipeline"""
import requests, json, time, os

url = 'http://127.0.0.1:5000/api/chat/file'
# Use the already-converted docx - no conversion needed  
file_path = r'C:\Users\12524\Desktop\Koto\workspace\documents\王宇轩-简历（美元)_converted.docx'

if not os.path.exists(file_path):
    print("Converted file not found! Run the .doc test first.")
    exit(1)

print(f'Testing with: {os.path.basename(file_path)} ({os.path.getsize(file_path):,} bytes)')
print('Sending request...')
t0 = time.time()

with open(file_path, 'rb') as f:
    files = [('file', (os.path.basename(file_path), f, 'application/octet-stream'))]
    data = {'session': 'koto_docx_test', 'message': '帮我修改批注', 'locked_task': '', 'locked_model': 'auto'}
    resp = requests.post(url, files=files, data=data, stream=True, timeout=(30, 600))

print(f'Status: {resp.status_code}')
ct = resp.headers.get('Content-Type', 'unknown')
print(f'Content-Type: {ct}')

if 'text/event-stream' not in ct:
    print(f'ERROR: {resp.text[:500]}')
    exit(1)

print('SSE confirmed. Streaming events...\n')
for line in resp.iter_lines(chunk_size=256, decode_unicode=True):
    if not line or not line.startswith('data: '):
        continue
    try:
        evt = json.loads(line[6:])
        etype = evt.get('type', '?')
        elapsed = time.time() - t0
        if etype == 'classification':
            print(f'[{elapsed:.0f}s] CLASSIFICATION: task_type={evt.get("task_type")} model={evt.get("model")}')
        elif etype == 'progress':
            print(f'[{elapsed:.0f}s] PROGRESS {evt.get("progress",0):3d}%: {evt.get("message","")[:80]}')
        elif etype == 'info':
            print(f'[{elapsed:.0f}s] INFO: {evt.get("message","")[:100]}')
        elif etype == 'token':
            content = evt.get('content', '')
            print(f'\n[{elapsed:.0f}s] RESULT SUMMARY:\n{content[:1000]}')
        elif etype == 'done':
            saved = evt.get('saved_files', [])
            print(f'\n[{elapsed:.0f}s] DONE! saved_files={saved}')
            for sf in saved:
                if os.path.exists(sf):
                    print(f'  ✅ File exists: {os.path.basename(sf)} ({os.path.getsize(sf):,} bytes)')
                    in_docs = 'workspace\\documents' in sf or 'workspace/documents' in sf
                    print(f'  {"✅ IN workspace/documents" if in_docs else "❌ NOT in workspace/documents"}: {sf}')
                else:
                    print(f'  ❌ File NOT found: {sf}')
            break
        elif etype == 'error':
            print(f'[{elapsed:.0f}s] ERROR: {evt.get("message", "")}')
            break
    except json.JSONDecodeError:
        pass

print(f'\nTotal elapsed: {time.time()-t0:.1f}s')
