"""Record installed locked dependency metadata and retain their bundled notices."""
from importlib import metadata
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
rows=[]
for line in (ROOT/'requirements.lock.txt').read_text().splitlines():
    if not line.strip() or line.startswith('#'):continue
    name,version=line.split('==')
    dist=metadata.distribution(name)
    if dist.version!=version:raise ValueError('Installed dependency differs from lockfile: '+name)
    notices=[]
    for entry in dist.files or []:
        if '.dist-info/' not in str(entry):continue
        if not any(word in entry.name.upper() for word in ('LICENSE','LICENCE','COPYING','NOTICE')):continue
        relative=Path(str(entry).split('.dist-info/',1)[1])
        dest=ROOT/'licenses/dependencies'/name/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(Path(dist.locate_file(entry)).read_bytes())
        notices.append(dest.relative_to(ROOT).as_posix())
    expression=dist.metadata.get('License-Expression') or (dist.metadata.get('License') or '').split('\n')[0][:160] or 'See bundled notices / package metadata'
    rows.append({'name':name,'version':version,'license_metadata':expression,'bundled_notices':notices,'binary_redistributed':False})
(ROOT/'docs/dependency-licenses.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'packages':len(rows),'notices':sum(len(row['bundled_notices']) for row in rows)}))
