from pathlib import Path
import json
import shutil
import zipfile
import hashlib

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'agrosem_results'
code=OUT/'code'
code.mkdir(exist_ok=True)
for filename in ['build_agrosem_analysis.py','agrosem_report_content.py','verify_agrosem_outputs.py','requirements-agrosem.txt']:
    shutil.copyfile(ROOT/filename,code/filename)
files=[OUT/f for f in ['README.md','01_План_і_ролі.docx','01_План_і_ролі.md',
       '02_Частина_1_аудит.docx','02_Частина_1_аудит.md','03_АГРОСЕМ_B_дані_та_аудит.xlsx',
       'manifest.json','metrics.json','validation_summary.json']]
for folder in ['data','charts','code']:
    files.extend(sorted((OUT/folder).glob('*')))
manifest={str(p.relative_to(OUT)):{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files if p.is_file()}
(OUT/'package_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
files.append(OUT/'package_manifest.json')
archive=OUT/'АГРОСЕМ_B_план_і_частина_1.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in files:
        if p.is_file(): z.write(p,str(p.relative_to(OUT)))
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'files':len(z.namelist()),'integrity':'PASS'},ensure_ascii=True))
