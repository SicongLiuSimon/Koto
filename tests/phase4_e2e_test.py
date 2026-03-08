#!/usr/bin/env python3
"""Create a small test docx and run E2E annotation test"""
import os, sys, docx, requests, json, time

# Create a small test docx if it doesn't exist
test_docx = r'C:\Users\12524\Desktop\Koto\web\uploads\test_clean_doc.docx'
if not os.path.exists(test_docx):
    d = docx.Document()
    d.add_heading('测试文档 - 员工绩效报告', 0)
    d.add_paragraph('本报告针对2024年第四季度的员工绩效进行综合评估。')
    d.add_heading('一、工作成果', 1)
    d.add_paragraph('该员工在本季度完成了多项重要项目，包括新产品的上线以及系统架构的优化。整体工作成果较前期有明显提升，展示了较强的执行力与专业能力。')
    d.add_paragraph('在团队协作方面，该员工积极参与了跨部门会议，并提出了多条优化建议，对项目按时交付起到了重要作用。')
    d.add_heading('二、存在问题', 1)
    d.add_paragraph('部分文档的撰写工作存在不够完善的地方，需要进一步提高技术文档的规范性。与此同时，在时间管理方面，偶尔出现了任务优先级判断不够准确的情况，导致部分工作未能及时推进。')
    d.add_paragraph('建议该员工在后续工作中加强时间规划能力，并在参与重要会议前做好充分准备。')
    d.add_heading('三、改进建议', 1)
    d.add_paragraph('首先，建议系统性地学习项目管理相关知识，提升对任务优先级的判断能力。其次，在文档撰写方面，可以参考公司标准模板，确保格式的统一性和内容的完整性。')
    d.add_paragraph('展望下一季度，建议设立明确的个人绩效目标，定期进行自我评估，并主动与上级沟通工作进展。')
    d.save(test_docx)
    print(f'Created test docx: {test_docx} ({os.path.getsize(test_docx)} bytes)')
else:
    print(f'Using existing: {test_docx} ({os.path.getsize(test_docx)} bytes)')

url = 'http://127.0.0.1:5000/api/chat/file'
print('Sending to annotation pipeline...')
t0 = time.time()

with open(test_docx, 'rb') as f:
    files = [('file', (os.path.basename(test_docx), f, 'application/octet-stream'))]
    data = {'session': 'koto_e2e_test', 'message': '帮我修改批注', 'locked_task': '', 'locked_model': 'auto'}
    resp = requests.post(url, files=files, data=data, stream=True, timeout=(30, 600))

print(f'Status: {resp.status_code}')
ct = resp.headers.get('Content-Type', '')
if 'text/event-stream' not in ct:
    print(f'ERROR: {resp.text[:300]}')
    sys.exit(1)

print('SSE confirmed.\n')
for line in resp.iter_lines(chunk_size=256, decode_unicode=True):
    if not line or not line.startswith('data: '):
        continue
    try:
        evt = json.loads(line[6:])
        etype = evt.get('type', '?')
        el = time.time() - t0
        if etype == 'classification':
            print(f'[{el:.0f}s] CLASSIFICATION: {evt.get("task_type")} / {evt.get("model")}')
        elif etype == 'progress':
            print(f'[{el:.0f}s] PROGRESS {evt.get("progress",0):3d}%: {evt.get("message","")[:80]}')
        elif etype == 'info':
            print(f'[{el:.0f}s] INFO: {evt.get("message","")[:120]}')
        elif etype == 'token':
            content = evt.get('content', '')
            print(f'\n[{el:.0f}s] SUMMARY:\n{content[:2000]}')
        elif etype == 'done':
            saved = evt.get('saved_files', [])
            print(f'\n[{el:.0f}s] DONE! total_time={evt.get("total_time",el):.1f}s')
            print(f'saved_files: {saved}')
            for sf in saved:
                in_docs = 'workspace' in sf and 'documents' in sf
                exists = os.path.exists(sf)
                print(f'  {"✅" if in_docs else "❌"} in workspace/documents: {os.path.basename(sf)}')
                print(f'  {"✅" if exists else "❌"} file exists: {sf}')
            break
        elif etype == 'error':
            print(f'[{el:.0f}s] ERROR: {evt.get("message", "")}')
            break
    except json.JSONDecodeError:
        pass

print(f'\nTotal: {time.time()-t0:.1f}s')
