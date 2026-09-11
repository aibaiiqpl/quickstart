# -*- coding: utf-8 -*-
"""技術仕様書 Markdown -> 提出用 HTML。ドキュメントで使用する記法のみ対応。"""
import io, re, sys, html

src = io.open(sys.argv[1], encoding='utf-8').read()
# 作業用の見出し・注記（先頭の H1 とその直後の引用）は提出物に含めない
i = src.index('## 出力制御装置技術仕様書')
lines = src[i:].split('\n')

def inline(t):
    t = html.escape(t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'`(.+?)`', r'<code>\1</code>', t)
    return t

def join(buf):
    """日本語は行末で連結しても空白を入れない。ASCII 同士のみ空白を入れる。"""
    out = ''
    for s in buf:
        if out and (out[-1].isascii() and out[-1].isalnum()) and (s[0].isascii() and s[0].isalnum()):
            out += ' '
        out += s
    return out

def cells(l):
    return [c.strip() for c in l.strip().strip('|').split('|')]

out, para, li, n = [], [], [], 0
def flush():
    global para, li
    if para:
        out.append('<p>%s</p>' % inline(join(para))); para = []
    if li:
        out.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % inline(join([x])) for x in li)); li = []

while n < len(lines):
    l = lines[n]; s = l.strip()
    if not s:
        flush(); n += 1; continue
    if s.startswith('#'):
        flush()
        lv = len(s) - len(s.lstrip('#'))
        out.append('<h%d>%s</h%d>' % (lv, inline(s[lv:].strip()), lv)); n += 1; continue
    if s == '---':
        flush(); n += 1; continue
    if s.startswith('- '):
        flush() if para else None
        li.append(s[2:]); n += 1; continue
    if s.startswith('|') and n + 1 < len(lines) and re.match(r'^\|[\s:|-]+\|$', lines[n+1].strip()):
        flush()
        hdr = cells(s); n += 2
        rows = []
        while n < len(lines) and lines[n].strip().startswith('|'):
            rows.append(cells(lines[n])); n += 1
        info = all(h == '' for h in hdr)          # 表紙の書誌事項表
        th = '' if info else '<thead><tr>%s</tr></thead>' % ''.join('<th>%s</th>' % inline(c) for c in hdr)
        tb = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % inline(c) for c in r) for r in rows)
        out.append('<table class="%s">%s<tbody>%s</tbody></table>' % ('info' if info else 'grid', th, tb))
        continue
    if s.startswith('>'):
        flush(); n += 1; continue
    para.append(s); n += 1
flush()

CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
body { font-family: "IPAPGothic","IPAGothic",sans-serif; font-size: 9.6pt; line-height: 1.75;
       color: #111; margin: 0; }
h2 { font-size: 17pt; text-align: center; letter-spacing: .18em; margin: 0 0 4mm;
     padding-bottom: 3mm; border-bottom: 2.4pt solid #111; }
h3 { font-size: 12.5pt; margin: 8mm 0 3mm; padding: 1.6mm 0 1.6mm 3mm;
     border-left: 3.6pt solid #111; background: #f2f2f2; page-break-after: avoid; }
h4 { font-size: 10.8pt; margin: 6mm 0 2.2mm; padding-bottom: 1.2mm;
     border-bottom: .8pt solid #999; page-break-after: avoid; }
h5 { font-size: 10pt; margin: 5mm 0 2mm; page-break-after: avoid; }
p  { margin: 0 0 2.6mm; text-align: justify; }
ul { margin: 0 0 3mm; padding-left: 6mm; }
li { margin-bottom: 1.2mm; }
code { font-family: monospace; font-size: 9pt; }
table { border-collapse: collapse; width: 100%; margin: 0 0 4mm;
        page-break-inside: avoid; font-size: 9.2pt; }
table.grid th, table.grid td { border: .7pt solid #666; padding: 1.5mm 2.2mm;
        vertical-align: top; line-height: 1.6; }
table.grid th { background: #e4e4e4; font-weight: bold; text-align: left; white-space: nowrap; }
table.info { width: 76%; margin: 6mm auto 8mm; }
table.info td { border: .7pt solid #666; padding: 2mm 3mm; }
table.info td:first-child { background: #f2f2f2; width: 38%; font-weight: bold; white-space: nowrap; }
"""
io.open(sys.argv[2], 'w', encoding='utf-8').write(
    '<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8">'
    '<title>出力制御装置技術仕様書</title><style>%s</style></head><body>%s</body></html>'
    % (CSS, '\n'.join(out)))
print('html ok')
