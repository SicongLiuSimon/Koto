#!/usr/bin/env python3
"""Test DOC_ANNOTATE pipeline E2E via API"""
import requests
import json
import time
import os

url = 'http://127.0.0.1:5000/api/chat/file'
file_path = r'C:\Users\12524\Desktop\Koto\web\uploads\王宇轩-简历（美元).doc'

if not os.path.exists(file_path):
    # Fallback to docx
    file_path = r'C:\Users\12524\Desktop\Koto\web\uploads\电影时间的计算解析：基于大视觉语言模型的电影连续性研究.docx'

print(f'Testing DOC_ANNOTATE pipeline with: {os.path.basename(file_path)}')
print(f'File size: {os.path.getsize(file_path):,} bytes')
print('Sending request...')
t0 = time.time()

with open(file_path, 'rb') as f:
    file_name = os.path.basename(file_path)
    files = [('file', (file_name, f, 'application/octet-stream'))]
    data = {
        'session': 'koto_test_annotation',
        'message': '帮我修改批注',
        'locked_task': '',
        'locked_model': 'auto'
    }
    resp = requests.post(url, files=files, data=data, stream=True, timeout=(30, 600))

print(f'Status: {resp.status_code}')
ct = resp.headers.get('Content-Type', 'unknown')
print(f'Content-Type: {ct}')

if 'text/event-stream' in ct:
    print('==> SSE stream confirmed! DOC_ANNOTATE pipeline is engaged.')
    print()
    lines_received = 0
    for line in resp.iter_lines(chunk_size=256):
        if line and line.startswith(b'data: '):
            try:
                evt = json.loads(line[6:])
                etype = evt.get('type', '?')
                if etype == 'classification':
                    print(f'  [CLASSIFICATION] task_type={evt.get("task_type")} model={evt.get("model")}')
                elif etype == 'progress':
                    pct = evt.get('progress', 0)
                    msg = evt.get('message', '')
                    detail = evt.get('detail', '')
                    print(f'  [PROGRESS {pct:3d}%] {msg}' + (f' | {detail}' if detail else ''))
                elif etype == 'info':
                    print(f'  [INFO] {evt.get("message", "")[:100]}')
                elif etype == 'token':
                    content = evt.get('content', '')
                    print(f'  [TOKEN] {content[:200]}...' if len(content) > 200 else f'  [TOKEN] {content}')
                elif etype == 'done':
                    saved = evt.get('saved_files', [])
                    elapsed = evt.get('total_time', time.time() - t0)
                    print(f'\n  [DONE] total_time={elapsed:.1f}s')
                    print(f'  saved_files={saved}')
                    if saved:
                        for sf in saved:
                            exists = os.path.exists(sf)
                            print(f'    File exists: {exists} -> {sf}')
                    break
                elif etype == 'error':
                    print(f'  [ERROR] {evt.get("message", "")}')
                    break
                lines_received += 1
                if lines_received > 500:
                    print('  (stopping preview - too many events)')
                    break
            except json.JSONDecodeError:
                pass
    print(f'\nTotal elapsed: {time.time()-t0:.1f}s')
else:
    body = resp.text[:1000]
    print(f'Non-SSE response (unexpected for DOC_ANNOTATE):')
    print(body)
    print(f'\nTotal elapsed: {time.time()-t0:.1f}s')

# Check what's in workspace/documents
print('\n--- Files in workspace/documents ---')
docs_dir = r'C:\Users\12524\Desktop\Koto\workspace\documents'
if os.path.exists(docs_dir):
    files_list = sorted(os.listdir(docs_dir), key=lambda x: os.path.getmtime(os.path.join(docs_dir, x)), reverse=True)
    for f in files_list[:10]:
        fpath = os.path.join(docs_dir, f)
        mtime = time.ctime(os.path.getmtime(fpath))
        size = os.path.getsize(fpath)
        print(f'  {f} ({size:,} bytes, {mtime})')
else:
    print(f'  docs_dir does not exist: {docs_dir}')
