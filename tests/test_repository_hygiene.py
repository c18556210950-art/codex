"""Offline snapshot hygiene checks; reports paths only, never secret values."""
import ast
import re
import subprocess
from pathlib import Path

root=Path(__file__).resolve().parents[1]
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
errors=[]
for name in filter(None,tracked):
    if '.private.' in name or name.endswith(('.pem','.key','.p12')) or name.startswith(('work/','outputs/')):
        errors.append(name+': private/generated file tracked')
    text=(root/name).read_text(encoding='utf-8',errors='replace')
    for match in re.finditer(r'/(?:car|car-link)/([A-Za-z0-9_-]{16,})',text):
        if not match.group(1).startswith(('REPLACE_','YOUR_')):errors.append(name+': embedded access URL')
    if name.startswith('src/') and any(t in text for t in ['bankKeepalive','keepaliveTick','keepalivePulse']):
        errors.append(name+': removed powerbank micro-movement present')
    if name.endswith('.py'):
        for node in ast.walk(ast.parse(text)):
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
                for target in node.targets:
                    if isinstance(target,ast.Name) and 'PASSWORD' in target.id.upper() and node.value.value and not node.value.value.startswith(('REPLACE_','YOUR_')):
                        errors.append(name+': literal password')
assert not errors,'\n'.join(errors)
print('Snapshot hygiene: no tracked private files, literal Python passwords, keyed control URLs or removed bank micro-movement')
