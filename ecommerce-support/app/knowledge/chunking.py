"""Markdown blocks, complete sentences and repeated table headers; no model dependencies."""
from dataclasses import replace
import re
from app.knowledge.types import ChunkDraft
from app.knowledge.text import embedding_text

SOFT_BYTES = 1200
OVERLAP_BYTES = 200
HARD_BYTES = 6000

class ChunkingError(ValueError):
    def __init__(self, source: str, reason: str):
        super().__init__(f'{source}: {reason}')


def _size(text):
    return len(text.encode('utf-8'))


def _cells(line):
    # Only unescaped pipes outside code spans are table separators.
    cells=[]; current=''; escaped=False; code=False
    for char in line.strip().strip('|'):
        if escaped:
            current+=char; escaped=False
        elif char=='\\':
            current+=char; escaped=True
        elif char=='`':
            code=not code; current+=char
        elif char=='|' and not code:
            cells.append(current.strip()); current=''
        else:
            current+=char
    return cells+[current.strip()]


def _separator(line):
    cells=_cells(line)
    return bool(cells) and all(re.fullmatch(r':?-{3,}:?', c) for c in cells)


def _sentences(text):
    # Do not interpret the decimal point in 9.99 as a sentence boundary.
    pattern=r'[。！？!?]+[”’"\']*|(?<!\d)\.(?!\d)[”’"\']*|(?<=\d)\.(?!\d)[”’"\']*'
    parts=[]; pos=0
    for match in re.finditer(pattern, text):
        parts.append(text[pos:match.end()]); pos=match.end()
    if pos<len(text): parts.append(text[pos:])
    return [p for p in parts if p.strip()]


def _pack_sentences(text, template):
    overhead=_size(embedding_text(replace(template,answer='')))
    atoms=_sentences(text)
    result=[]; group=[]; i=0
    while i<len(atoms):
        atom=atoms[i]
        if overhead+_size(atom)>HARD_BYTES:
            raise ChunkingError(template.section_path or '', 'too_large: unsplittable sentence')
        if group and overhead+_size(''.join(group)+atom)>SOFT_BYTES:
            result.append(replace(template,answer=''.join(group).strip()))
            suffix=[]
            for sentence in reversed(group):
                if _size(''.join([sentence,*suffix]))>OVERLAP_BYTES: break
                suffix.insert(0,sentence)
            while suffix and overhead+_size(''.join(suffix)+atom)>SOFT_BYTES:
                suffix.pop(0)
            group=suffix
        group.append(atom); i+=1
    if group: result.append(replace(template,answer=''.join(group).strip()))
    return result


def _pack_table(lines, template):
    header='\n'.join(lines[:2]); group=[]; result=[]
    def draft(rows): return replace(template,answer='\n'.join([header,*rows]))
    if _size(embedding_text(draft([])))>HARD_BYTES:
        raise ChunkingError(template.section_path or '', 'too_large: table header')
    for row in lines[2:]:
        if _size(embedding_text(draft([row])))>HARD_BYTES:
            raise ChunkingError(template.section_path or '', 'too_large: table row')
        if group and _size(embedding_text(draft([*group,row])))>SOFT_BYTES:
            result.append(draft(group)); group=[]
        group.append(row)
    if group or not result: result.append(draft(group))
    return result


def split_markdown(text: str, document_title: str, content_type: str) -> list[ChunkDraft]:
    lines=text.splitlines(); stack=[]; output=[]; key=False; i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip(): i+=1; continue
        if line.strip()=='<!-- key-clause -->': key=True; i+=1; continue
        heading=re.match(r'^ {0,3}(#{1,6})\s+(.+?)\s*#*$',line)
        if heading:
            level=len(heading[1])
            while stack and stack[-1][0]>=level: stack.pop()
            stack.append((level,heading[2])); i+=1; continue
        titles=[x[1] for x in stack] or [document_title]
        template=ChunkDraft(' / '.join(titles[:-1]) or document_title,titles[-1],'',
                            ' / '.join(titles),content_type,key)
        if len(template.category)>255 or len(template.section_path)>512 or len(content_type)>32:
            raise ChunkingError(document_title,'path_or_type_too_long')
        key=False
        fence=re.match(r'^ {0,3}(`{3,}|~{3,})',line)
        if fence:
            start=i; i+=1; closed=False
            closing=r'^ {0,3}'+re.escape(fence[1][0])+r'{'+str(len(fence[1]))+r',}\s*$'
            while i<len(lines):
                if re.match(closing,lines[i]): i+=1; closed=True; break
                i+=1
            if not closed: raise ChunkingError(template.section_path,'unclosed_fence')
            row=replace(template,answer='\n'.join(lines[start:i]))
            if _size(embedding_text(row))>HARD_BYTES: raise ChunkingError(template.section_path,'too_large: code block')
            output.append(row); continue
        if i+1<len(lines) and '|' in line and _separator(lines[i+1]):
            start=i; i+=2
            while i<len(lines) and lines[i].strip() and '|' in lines[i]:
                if len(_cells(lines[i]))!=len(_cells(line)):
                    raise ChunkingError(template.section_path,'malformed_table')
                i+=1
            output.extend(_pack_table(lines[start:i],template)); continue
        start=i; i+=1
        while i<len(lines) and lines[i].strip():
            if re.match(r'^ {0,3}(#{1,6}\s|`{3,}|~{3,})',lines[i]) or lines[i].strip()=='<!-- key-clause -->': break
            if i+1<len(lines) and '|' in lines[i] and _separator(lines[i+1]): break
            i+=1
        output.extend(_pack_sentences('\n'.join(lines[start:i]),template))
    return output
