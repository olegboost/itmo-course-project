// Minimal Markdown -> HTML converter for BRD documents.
// Shared between the n8n Code node (WF04, inlined by build_workflows.py) and local tooling.
// Supports: headings, tables, bullet/numbered lists, checkboxes, blockquotes, hr,
// fenced code, **bold**, *italic*, `code`, [links](url).
// BEGIN md2html
function md2html(md) {
  const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, '$1<i>$2</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2">$1</a>')
    .replace(/^\[ \]\s*/, '&#9744; ')
    .replace(/^\[x\]\s*/i, '&#9745; ');
  const cells = (row) => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const TD = 'style="border:1px solid #999;padding:4px 6px;vertical-align:top"';
  const TH = 'style="border:1px solid #999;padding:4px 6px;background:#e8eef7;font-weight:bold"';

  const lines = String(md || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(esc(lines[i++]));
      i++;
      out.push('<pre style="font-family:monospace;background:#f4f4f4">' + buf.join('\n') + '</pre>');
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2].trim())}</h${h[1].length}>`); i++; continue; }
    if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue; }
    if (/^\s*\|/.test(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(cells(lines[i++]));
      let t = '<table style="border-collapse:collapse;width:100%"><tr>' +
        head.map((c) => `<th ${TH}>${inline(c)}</th>`).join('') + '</tr>';
      for (const r of rows) t += '<tr>' + head.map((_, k) => `<td ${TD}>${inline(r[k] || '')}</td>`).join('') + '</tr>';
      out.push(t + '</table>');
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const re = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
      const items = [];
      while (i < lines.length && re.test(lines[i])) {
        let text = lines[i++].replace(re, '');
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) text += ' ' + lines[i++].trim();
        items.push(`<li>${inline(text)}</li>`);
      }
      out.push((ordered ? '<ol>' : '<ul>') + items.join('') + (ordered ? '</ol>' : '</ul>'));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(inline(lines[i++].replace(/^\s*>\s?/, '')));
      out.push('<blockquote style="color:#555;border-left:3px solid #ccc;padding-left:8px">' + buf.join('<br>') + '</blockquote>');
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*[-*+]\s|\s*\d+[.)]\s|\s*\||\s*>|\s*```)/.test(lines[i])) buf.push(inline(lines[i++].trim()));
    if (!buf.length) { out.push(`<p>${inline(lines[i++])}</p>`); continue; }
    out.push(`<p>${buf.join('<br>')}</p>`);
  }
  return '<html><head><meta charset="utf-8"></head><body style="font-family:Arial,sans-serif;font-size:11pt">' +
    out.join('\n') + '</body></html>';
}
// END md2html

if (typeof module !== 'undefined' && require.main === module) {
  const fs = require('fs');
  const [, , src, dst] = process.argv;
  if (!src || !dst) { console.error('usage: node md2html.js input.md output.html'); process.exit(1); }
  fs.writeFileSync(dst, md2html(fs.readFileSync(src, 'utf8')));
}
if (typeof module !== 'undefined') module.exports = { md2html };
